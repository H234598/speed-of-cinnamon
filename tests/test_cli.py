# mypy: ignore-errors
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import tomllib
import tempfile
import unittest
import wave
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from speed_of_cinnamon import cli
from speed_of_cinnamon.alarms import (
    MAX_ALARM_NAME_CHARS,
    MAX_ALARM_ID_CHARS,
    add_alarm,
    list_alarm_payload,
    save_alarm_store,
)
from speed_of_cinnamon.recorder import InputSource, RecorderCommand
from speed_of_cinnamon.state import RecordingState, StateStore


class CliTest(unittest.TestCase):
    def test_write_json_atomic_sets_private_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "payload.json"
            cli._write_json_atomic(path, {"status": "ok"}, max_bytes=1_000_000)
            mode = path.stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)

    def test_write_text_atomic_sets_private_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "note.txt"
            cli._write_text_atomic(path, "private")
            mode = path.stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)

    def test_version_option_prints_current_version(self) -> None:
        parser = cli.build_parser()
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            with self.assertRaises(SystemExit) as exc:
                parser.parse_args(["--version"])
        self.assertEqual(exc.exception.code, 0)
        self.assertEqual(stdout.getvalue().strip(), f"speed-of-cinnamon {cli.__version__}")

    def test_coerce_log_level_from_environment(self) -> None:
        with mock.patch.dict("speed_of_cinnamon.cli.os.environ", {"SPEED_OF_CINNAMON_LOG_LEVEL": "INFO"}):
            self.assertEqual(cli._coerce_log_level_from_environment(), "info")
        with mock.patch.dict("speed_of_cinnamon.cli.os.environ", {"SPEED_OF_CINNAMON_LOG_LEVEL": "info\n"}):
            self.assertEqual(cli._coerce_log_level_from_environment(), cli.DEFAULT_LOG_LEVEL)
        with mock.patch.dict("speed_of_cinnamon.cli.os.environ", {"SPEED_OF_CINNAMON_LOG_LEVEL": "trace"}):
            self.assertEqual(cli._coerce_log_level_from_environment(), cli.DEFAULT_LOG_LEVEL)

    def test_version_consistency_between_metadata_and_package(self) -> None:
        project_version = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        self.assertEqual(project_version, cli.__version__)
        applet_metadata = json.loads(Path("files/speed-of-cinnamon@H234598/metadata.json").read_text(encoding="utf-8"))
        applet_version = applet_metadata["version"]
        self.assertEqual(project_version, applet_version)
        self.assertIn(f"Version: {project_version}", applet_metadata["comments"])
        applet_schema = json.loads(Path("files/speed-of-cinnamon@H234598/settings-schema.json").read_text(encoding="utf-8"))
        self.assertIn("about-page", applet_schema["layout"]["pages"])
        self.assertIn("about-version", applet_schema["layout"]["about-section"]["keys"])
        self.assertIn(f"Version: {project_version}", applet_schema["about-version"]["description"])

    def _write_wav(self, path: Path, samples: list[int]) -> None:
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(b"".join(sample.to_bytes(2, "little", signed=True) for sample in samples))

    def test_insert_text_can_be_disabled(self) -> None:
        with redirect_stdout(io.StringIO()):
            code = cli.run(["insert-text", "hello", "--insert-method", "none", "--json"])
        self.assertEqual(code, 0)

    @mock.patch("speed_of_cinnamon.cli.insert_text", return_value=True)
    def test_insert_text_can_sanitize_special_chars(self, mocked_insert: mock.Mock) -> None:
        with redirect_stdout(io.StringIO()):
            code = cli.run(["insert-text", "Grüße", "--insert-method", "none", "--sanitize-special-chars", "--json"])
        self.assertEqual(code, 0)
        mocked_insert.assert_called_once_with("Grusse", "none", 8)

    def test_insert_text_rejects_overlong_text(self) -> None:
        with redirect_stdout(io.StringIO()) as capture:
            code = cli.run([
                "insert-text",
                "x" * (cli.MAX_TRANSCRIBER_TEXT_CHARS + 10),
                "--insert-method",
                "none",
                "--json",
            ])
        payload = json.loads(capture.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("text is too large", payload["error"])

    def test_insert_text_rejects_null_bytes(self) -> None:
        with redirect_stdout(io.StringIO()) as capture:
            code = cli.run(["insert-text", "hello\x00", "--insert-method", "none", "--json"])
        payload = json.loads(capture.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("contains invalid null byte", payload["error"])

    def test_insert_text_rejects_negative_typing_delay(self) -> None:
        with redirect_stdout(io.StringIO()) as capture:
            code = cli.run(["insert-text", "hello", "--insert-method", "none", "--typing-delay-ms", "-1", "--json"])
        payload = json.loads(capture.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("typing-delay-ms must be at least 0", payload["error"])

    def test_insert_text_rejects_excessive_typing_delay(self) -> None:
        with redirect_stdout(io.StringIO()) as capture:
            code = cli.run([
                "insert-text",
                "hello",
                "--insert-method",
                "none",
                "--typing-delay-ms",
                str(cli.MAX_TYPING_DELAY_MS + 1),
                "--json",
            ])
        payload = json.loads(capture.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("typing-delay-ms must be at most", payload["error"])

    def test_transcribe_file_with_command_template(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf test",
                    "--post-process-command",
                    "python3 -c 'import sys; print(sys.stdin.read().upper())'",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
            saved = Path(payload["transcript_path"]).read_text(encoding="utf-8").strip()
        self.assertEqual(code, 0)
        self.assertEqual(payload["transcript"], "TEST")
        self.assertEqual(saved, "TEST")

    @mock.patch("speed_of_cinnamon.cli.transcribe", return_value="ok")
    @mock.patch("speed_of_cinnamon.cli.validate_audio_file")
    def test_transcribe_file_accepts_transcriber_aliases(self, mocked_validate: mock.Mock, mocked_transcribe: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            stdout = io.StringIO()
            mocked_validate.return_value = audio
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--transcriber",
                    "openai",
                    "--json",
                ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["transcript"], "ok")
        mocked_transcribe.assert_called_once_with(
            audio_path=audio,
            language="en",
            text_path=mock.ANY,
            command_template="",
            backend="whisper",
            whisper_model="",
            personal_context="",
            vocabulary="",
        )

    @mock.patch("speed_of_cinnamon.cli.post_process_text", return_value="polished")
    @mock.patch("speed_of_cinnamon.cli.transcribe", return_value="raw")
    @mock.patch("speed_of_cinnamon.cli.validate_audio_file")
    def test_transcribe_file_uses_separate_openai_compatible_text_model(
        self,
        mocked_validate: mock.Mock,
        mocked_transcribe: mock.Mock,
        mocked_post_process: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            stdout = io.StringIO()
            mocked_validate.return_value = audio
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--transcriber",
                    "openai-compatible",
                    "--post-process-backend",
                    "openai-compatible",
                    "--openai-compatible-model",
                    "gpt-4o-transcribe",
                    "--openai-compatible-text-model",
                    "gpt-4o-mini",
                    "--openai-compatible-api-key",
                    "secret",
                    "--json",
                ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["transcript"], "polished")
        self.assertEqual(mocked_transcribe.call_args.kwargs["openai_compatible_model"], "gpt-4o-transcribe")
        self.assertIs(mocked_transcribe.call_args.kwargs["openai_compatible_flex_processing"], True)
        self.assertEqual(mocked_post_process.call_args.args[9], "gpt-4o-mini")
        self.assertEqual(mocked_post_process.call_args.args[11], "secret")
        self.assertIs(mocked_post_process.call_args.args[12], True)

    @mock.patch("speed_of_cinnamon.cli.post_process_text", return_value="polished")
    @mock.patch("speed_of_cinnamon.cli.transcribe", return_value="raw")
    @mock.patch("speed_of_cinnamon.cli.validate_audio_file")
    def test_transcribe_file_can_disable_openai_compatible_flex_processing(
        self,
        mocked_validate: mock.Mock,
        mocked_transcribe: mock.Mock,
        mocked_post_process: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            stdout = io.StringIO()
            mocked_validate.return_value = audio
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--transcriber",
                    "openai-compatible",
                    "--post-process-backend",
                    "openai-compatible",
                    "--openai-compatible-model",
                    "gpt-4o-transcribe",
                    "--openai-compatible-text-model",
                    "gpt-4o-mini",
                    "--openai-compatible-api-key",
                    "secret",
                    "--no-openai-compatible-flex-processing",
                    "--json",
                ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["transcript"], "polished")
        self.assertIs(mocked_transcribe.call_args.kwargs["openai_compatible_flex_processing"], False)
        self.assertIs(mocked_post_process.call_args.args[12], False)

    @mock.patch("speed_of_cinnamon.cli.post_process_text", return_value="polished")
    @mock.patch("speed_of_cinnamon.cli.transcribe", return_value="raw")
    @mock.patch("speed_of_cinnamon.cli.validate_audio_file")
    def test_transcribe_file_defaults_openai_compatible_text_model_to_gpt_4o_mini(
        self,
        mocked_validate: mock.Mock,
        mocked_transcribe: mock.Mock,
        mocked_post_process: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            stdout = io.StringIO()
            mocked_validate.return_value = audio
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--transcriber",
                    "openai-compatible",
                    "--post-process-backend",
                    "openai-compatible",
                    "--openai-compatible-model",
                    "gpt-4o-transcribe",
                    "--openai-compatible-api-key",
                    "secret",
                    "--json",
                ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["transcript"], "polished")
        self.assertEqual(mocked_transcribe.call_args.kwargs["openai_compatible_model"], "gpt-4o-transcribe")
        self.assertIs(mocked_transcribe.call_args.kwargs["openai_compatible_flex_processing"], True)
        self.assertEqual(mocked_post_process.call_args.args[9], "gpt-4o-mini")
        self.assertEqual(mocked_post_process.call_args.args[11], "secret")
        self.assertIs(mocked_post_process.call_args.args[12], True)

    @mock.patch("speed_of_cinnamon.cli.transcribe", return_value="ok")
    @mock.patch("speed_of_cinnamon.cli.validate_audio_file")
    def test_transcribe_file_accepts_command_alias(self, mocked_validate: mock.Mock, mocked_transcribe: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            stdout = io.StringIO()
            mocked_validate.return_value = audio
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--transcriber",
                    "template",
                    "--transcriber-command",
                    "printf ok",
                    "--json",
                ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["transcript"], "ok")
        self.assertEqual(mocked_transcribe.call_args.kwargs["backend"], "command")

    @mock.patch("speed_of_cinnamon.cli.transcribe", return_value="ok")
    @mock.patch("speed_of_cinnamon.cli.validate_audio_file")
    def test_transcribe_file_accepts_faster_whisper_alias(self, mocked_validate: mock.Mock, mocked_transcribe: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            stdout = io.StringIO()
            mocked_validate.return_value = audio
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--transcriber",
                    "faster-whisper",
                    "--json",
                ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["transcript"], "ok")
        mocked_transcribe.assert_called_once_with(
            audio_path=audio,
            language="en",
            text_path=mock.ANY,
            command_template="",
            backend="faster-whisper",
            whisper_model="",
            personal_context="",
            vocabulary="",
        )

    def test_transcribe_file_passes_personalization_to_post_process(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            stdout = io.StringIO()
            command = "python3 -c \"import os, sys; print(sys.stdin.read().strip() + '|' + os.environ['SPEED_OF_CINNAMON_VOCABULARY'])\""
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf raw",
                    "--post-process-command",
                    command,
                    "--personal-context",
                    "Use project terms.",
                    "--vocabulary",
                    "PipeWire",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["transcript"], "raw|PipeWire")

    def test_transcribe_file_rejects_transcript_write_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            stdout = io.StringIO()
            with (
                mock.patch("speed_of_cinnamon.cli.os.replace", side_effect=OSError("disk full")),
                mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}),
                redirect_stdout(stdout),
            ):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf hello",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("failed to write transcript file", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_input_sources")
    def test_list_inputs_outputs_sources(self, mocked_sources: mock.Mock) -> None:
        mocked_sources.return_value = [
            InputSource(
                id="11",
                name="alsa_input.usb-mic.analog-stereo",
                description="USB Microphone",
                driver="PipeWire",
                state="RUNNING",
                default=True,
            )
        ]
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run(["list-inputs", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["sources"][0]["name"], "alsa_input.usb-mic.analog-stereo")
        self.assertTrue(payload["sources"][0]["default"])

    @mock.patch("speed_of_cinnamon.cli.list_input_sources", return_value="invalid")
    def test_list_inputs_rejects_non_list_sources(self, mocked_sources: mock.Mock) -> None:
        with redirect_stdout(io.StringIO()) as capture:
            code = cli.run(["list-inputs", "--json"])
        payload = json.loads(capture.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("input sources must be a list", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_input_sources", return_value=[object()])
    def test_list_inputs_rejects_invalid_source_entry(self, mocked_sources: mock.Mock) -> None:
        with redirect_stdout(io.StringIO()) as capture:
            code = cli.run(["list-inputs", "--json"])
        payload = json.loads(capture.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("input source id must be text", payload["error"])

    def test_models_lists_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["models", "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertGreater(len(payload["models"]), 0)
        self.assertEqual(payload["models"][0]["name"], "tiny.en")
        self.assertFalse(payload["models"][0]["downloaded"])

    @mock.patch("speed_of_cinnamon.cli.list_models", return_value="invalid")
    def test_models_rejects_non_list_models_payload(self, mocked_models: mock.Mock) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run(["models", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("model payload must be a list", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_models", return_value=["invalid"])
    def test_models_rejects_invalid_model_entry(self, mocked_models: mock.Mock) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run(["models", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("model payload entry must be an object", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.transcribe", return_value="hallo welt")
    def test_benchmark_models_reports_runtime_and_text(self, mocked_transcribe: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            model_path = Path(tmp) / "ggml-tiny.bin"
            self._write_wav(audio, [0, 100, -100])
            model_path.write_bytes(b"model")
            stdout = io.StringIO()
            with (
                mock.patch("speed_of_cinnamon.cli.model_path", return_value=model_path),
                mock.patch("speed_of_cinnamon.cli.model_status", return_value={"downloaded": True}),
                mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}),
                redirect_stdout(stdout),
            ):
                code = cli.run([
                    "benchmark-models",
                    str(audio),
                    "--language",
                    "de",
                    "--models",
                    "tiny",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "done")
        self.assertEqual(payload["fastest_model"], "tiny")
        self.assertEqual(payload["results"][0]["model"], "tiny")
        self.assertEqual(payload["results"][0]["transcript"], "hallo welt")
        self.assertTrue(payload["results"][0]["ok"])
        mocked_transcribe.assert_called_once()
        self.assertEqual(mocked_transcribe.call_args.kwargs["backend"], "whisper-cpp")
        self.assertEqual(mocked_transcribe.call_args.kwargs["whisper_model"], str(model_path))

    def test_benchmark_models_reports_missing_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            model_path = Path(tmp) / "missing.bin"
            self._write_wav(audio, [0, 100, -100])
            stdout = io.StringIO()
            with (
                mock.patch("speed_of_cinnamon.cli.model_path", return_value=model_path),
                mock.patch("speed_of_cinnamon.cli.model_status", return_value={"downloaded": False}),
                redirect_stdout(stdout),
            ):
                code = cli.run([
                    "benchmark-models",
                    str(audio),
                    "--models",
                    "tiny",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["results"][0]["model"], "tiny")
        self.assertIn("not downloaded", payload["results"][0]["error"])

    @mock.patch("speed_of_cinnamon.cli.list_ollama_models")
    def test_text_models_lists_local_ollama_models(self, mocked_list: mock.Mock) -> None:
        mocked_list.return_value = {
            "available": True,
            "models": [{"name": "llama3.2:3b"}],
            "message": "Ollama models loaded",
        }
        stdout = io.StringIO()
        with (
            mock.patch("speed_of_cinnamon.cli.shutil.which", return_value="/usr/bin/ollama"),
            redirect_stdout(stdout),
        ):
            code = cli.run(["text-models", "--ollama-url", "http://localhost:11434", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["backend"], "ollama")
        self.assertEqual(payload["url"], "http://localhost:11434")
        self.assertEqual(payload["models"][0]["name"], "llama3.2:3b")
        mocked_list.assert_called_once_with("http://localhost:11434")

    @mock.patch("speed_of_cinnamon.cli.list_ollama_models")
    def test_text_models_reports_missing_local_ollama_command(self, mocked_list: mock.Mock) -> None:
        mocked_list.return_value = {
            "available": False,
            "models": [],
            "message": "Ollama is not reachable at http://127.0.0.1:11434",
        }
        stdout = io.StringIO()
        with (
            mock.patch("speed_of_cinnamon.cli.shutil.which", return_value=None),
            redirect_stdout(stdout),
        ):
            code = cli.run(["text-models", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertFalse(payload["available"])
        self.assertIn("Ollama command is not available", payload["message"])
        mocked_list.assert_called_once_with(cli.DEFAULT_OLLAMA_URL)

    @mock.patch("speed_of_cinnamon.cli.list_ollama_models")
    def test_text_models_reports_missing_local_ollama_command_when_path_validation_fails(self, mocked_list: mock.Mock) -> None:
        mocked_list.return_value = {
            "available": False,
            "models": [],
            "message": "Ollama is not reachable at http://127.0.0.1:11434",
        }
        stdout = io.StringIO()
        with (
            mock.patch("speed_of_cinnamon.cli._command_path", side_effect=RuntimeError("command path is not trusted")),
            redirect_stdout(stdout),
        ):
            code = cli.run(["text-models", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertFalse(payload["available"])
        self.assertIn("Ollama command is not available", payload["message"])
        mocked_list.assert_called_once_with(cli.DEFAULT_OLLAMA_URL)

    @mock.patch("speed_of_cinnamon.cli.list_ollama_models")
    def test_text_models_rejects_overlong_ollama_url(self, mocked_list: mock.Mock) -> None:
        long_url = "http://localhost:11434/" + ("x" * (cli.MAX_URL_CHARS + 10))
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run(["text-models", "--ollama-url", long_url, "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("ollama url is too large", payload["error"])
        mocked_list.assert_not_called()

    def test_text_models_rejects_null_ollama_url(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run(["text-models", "--ollama-url", "http://localhost:11434\x00", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("contains invalid null byte", payload["error"])

    def test_install_text_model_pulls_ollama_model(self) -> None:
        completed = mock.Mock(returncode=0, stdout="ok", stderr="")
        stdout = io.StringIO()
        with (
            mock.patch("speed_of_cinnamon.cli.shutil.which", return_value="/usr/bin/ollama"),
            mock.patch("speed_of_cinnamon.cli.subprocess.run", return_value=completed) as mocked_run,
            mock.patch.dict("os.environ", {"LD_PRELOAD": "bad", "PYTHONPATH": "/tmp/evil"}, clear=False),
            redirect_stdout(stdout),
        ):
            code = cli.run([
                "install-text-model",
                "--model",
                "llama3.2:3b",
                "--ollama-url",
                "http://localhost:11434",
                "--json",
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "done")
        self.assertEqual(payload["model"], "llama3.2:3b")
        mocked_run.assert_called_once()
        self.assertEqual(mocked_run.call_args.args[0], ["/usr/bin/ollama", "pull", "llama3.2:3b"])
        self.assertEqual(mocked_run.call_args.kwargs["env"]["OLLAMA_HOST"], "http://localhost:11434")
        self.assertNotIn("LD_PRELOAD", mocked_run.call_args.kwargs["env"])
        self.assertNotIn("PYTHONPATH", mocked_run.call_args.kwargs["env"])

    def test_install_text_model_rejects_oversized_stdout(self) -> None:
        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            stdout = kwargs["stdout"]
            stderr = kwargs["stderr"]
            if not isinstance(stdout, object) or not isinstance(stderr, object):
                raise RuntimeError("expected file handles")
            stdout.write(b"x" * (cli.MAX_LOG_EXCERPT_CHARS + 1))
            stderr.write(b"")
            return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")

        stdout = io.StringIO()
        with (
            mock.patch("speed_of_cinnamon.cli.shutil.which", return_value="/usr/bin/ollama"),
            mock.patch("speed_of_cinnamon.cli.subprocess.run", side_effect=fake_run),
            redirect_stdout(stdout),
        ):
            code = cli.run([
                "install-text-model",
                "--model",
                "llama3.2:3b",
                "--ollama-url",
                "http://localhost:11434",
                "--json",
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("ollama pull stdout exceeded", payload["error"])

    def test_install_text_model_rejects_oversized_stderr(self) -> None:
        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            stdout = kwargs["stdout"]
            stderr = kwargs["stderr"]
            if not isinstance(stdout, object) or not isinstance(stderr, object):
                raise RuntimeError("expected file handles")
            stdout.write(b"ok")
            stderr.write(b"x" * (cli.MAX_LOG_EXCERPT_CHARS + 1))
            return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")

        stdout = io.StringIO()
        with (
            mock.patch("speed_of_cinnamon.cli.shutil.which", return_value="/usr/bin/ollama"),
            mock.patch("speed_of_cinnamon.cli.subprocess.run", side_effect=fake_run),
            redirect_stdout(stdout),
        ):
            code = cli.run([
                "install-text-model",
                "--model",
                "llama3.2:3b",
                "--ollama-url",
                "http://localhost:11434",
                "--json",
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("ollama pull stderr exceeded", payload["error"])

    def test_install_text_model_rejects_stdout_utf8_errors(self) -> None:
        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            stdout = kwargs["stdout"]
            stderr = kwargs["stderr"]
            if not isinstance(stdout, object) or not isinstance(stderr, object):
                raise RuntimeError("expected file handles")
            stdout.write(b"\xff")
            stderr.write(b"")
            return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")

        stdout = io.StringIO()
        with (
            mock.patch("speed_of_cinnamon.cli.shutil.which", return_value="/usr/bin/ollama"),
            mock.patch("speed_of_cinnamon.cli.subprocess.run", side_effect=fake_run),
            redirect_stdout(stdout),
        ):
            code = cli.run([
                "install-text-model",
                "--model",
                "llama3.2:3b",
                "--ollama-url",
                "http://localhost:11434",
                "--json",
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("ollama pull stdout is not valid UTF-8", payload["error"])

    def test_install_text_model_rejects_stderr_null_bytes(self) -> None:
        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            stdout = kwargs["stdout"]
            stderr = kwargs["stderr"]
            if not isinstance(stdout, object) or not isinstance(stderr, object):
                raise RuntimeError("expected file handles")
            stdout.write(b"ok")
            stderr.write(b"bad\x00")
            return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")

        stdout = io.StringIO()
        with (
            mock.patch("speed_of_cinnamon.cli.shutil.which", return_value="/usr/bin/ollama"),
            mock.patch("speed_of_cinnamon.cli.subprocess.run", side_effect=fake_run),
            redirect_stdout(stdout),
        ):
            code = cli.run([
                "install-text-model",
                "--model",
                "llama3.2:3b",
                "--ollama-url",
                "http://localhost:11434",
                "--json",
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("ollama pull stderr contains invalid null byte", payload["error"])

    def test_install_text_model_rejects_missing_ollama_command(self) -> None:
        stdout = io.StringIO()
        with (
            mock.patch("speed_of_cinnamon.cli.shutil.which", return_value=None),
            redirect_stdout(stdout),
        ):
            code = cli.run(["install-text-model", "--model", "llama3.2:3b", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("ollama command is not available", payload["error"])

    def test_install_text_model_rejects_untrusted_command_path(self) -> None:
        stdout = io.StringIO()
        with (
            mock.patch(
                "speed_of_cinnamon.cli._command_path",
                side_effect=RuntimeError("command path is not trusted"),
            ),
            redirect_stdout(stdout),
        ):
            code = cli.run(["install-text-model", "--model", "llama3.2:3b", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("ollama command is not available", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_openai_compatible_models")
    def test_text_models_rejects_overlong_openai_url(self, mocked_list: mock.Mock) -> None:
        long_url = "http://127.0.0.1:8000/" + ("x" * (cli.MAX_URL_CHARS + 10))
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run([
                "text-models",
                "--backend",
                "openai-compatible",
                "--openai-compatible-url",
                long_url,
                "--json",
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("openai-compatible url is too large", payload["error"])
        mocked_list.assert_not_called()

    @mock.patch("speed_of_cinnamon.cli.list_openai_compatible_models")
    def test_text_models_rejects_overlong_openai_api_key(self, mocked_list: mock.Mock) -> None:
        long_key = "x" * (cli.MAX_OPENAI_COMPATIBLE_API_KEY_CHARS + 1)
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run([
                "text-models",
                "--backend",
                "openai-compatible",
                "--openai-compatible-api-key",
                long_key,
                "--json",
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("openai-compatible API key is too large", payload["error"])
        mocked_list.assert_not_called()

    def test_text_models_rejects_null_openai_url(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run([
                "text-models",
                "--backend",
                "openai-compatible",
                "--openai-compatible-url",
                "http://127.0.0.1:8000\x00",
                "--json",
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("contains invalid null byte", payload["error"])

    def test_text_models_rejects_non_http_openai_url(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run([
                "text-models",
                "--backend",
                "openai-compatible",
                "--openai-compatible-url",
                "ftp://127.0.0.1:8000/v1",
                "--json",
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("openai-compatible url must use http:// or https://", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_openai_compatible_models")
    def test_text_models_lists_openai_compatible_models(self, mocked_list: mock.Mock) -> None:
        mocked_list.return_value = {
            "available": True,
            "models": [{"name": "local-llama"}],
            "message": "OpenAI-compatible models loaded",
        }
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run([
                "text-models",
                "--backend",
                "openai-compatible",
                "--openai-compatible-url",
                "http://127.0.0.1:8000/v1",
                "--openai-compatible-api-key",
                "secret",
                "--json",
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["backend"], "openai-compatible")
        self.assertEqual(payload["url"], "http://127.0.0.1:8000/v1")
        self.assertEqual(payload["models"][0]["name"], "local-llama")
        mocked_list.assert_called_once_with("http://127.0.0.1:8000/v1", api_key="secret")

    @mock.patch("speed_of_cinnamon.cli.list_ollama_models", return_value="invalid")
    def test_text_models_rejects_non_object_ollama_payload(self, mocked_list: mock.Mock) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run(["text-models", "--ollama-url", "http://localhost:11434", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("text models payload must be an object", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_ollama_models", return_value={
        "available": "yes",
        "models": [{"name": "llama3.2:3b"}],
        "message": "Ollama models loaded",
    })
    def test_text_models_rejects_ollama_invalid_available(self, mocked_list: mock.Mock) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run(["text-models", "--ollama-url", "http://localhost:11434", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("text models payload available must be a boolean", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_ollama_models", return_value={
        "available": True,
        "models": "invalid",
        "message": "Ollama models loaded",
    })
    def test_text_models_rejects_ollama_invalid_models(self, mocked_list: mock.Mock) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run(["text-models", "--ollama-url", "http://localhost:11434", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("model payload must be a list", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_ollama_models", return_value={
        "available": True,
        "models": [{"detail": 1}],
        "message": "Ollama models loaded",
    })
    def test_text_models_rejects_ollama_invalid_model_entry(self, mocked_list: mock.Mock) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run(["text-models", "--ollama-url", "http://localhost:11434", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("model name must be text", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_ollama_models", return_value={
        "available": True,
        "models": [{"name": "llama3.2:3b"}],
        "message": 123,
    })
    def test_text_models_rejects_ollama_invalid_message(self, mocked_list: mock.Mock) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run(["text-models", "--ollama-url", "http://localhost:11434", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("text models payload message must be text", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_ollama_models", return_value={
        "available": True,
        "models": [{"name": "llama3.2:3b"}],
        "message": "contains\x00",
    })
    def test_text_models_rejects_ollama_invalid_message_bytes(self, mocked_list: mock.Mock) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run(["text-models", "--ollama-url", "http://localhost:11434", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("text models payload message contains invalid null byte", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_ollama_models", return_value={
        "available": True,
        "models": [{"name": "llama3.2:3b"}],
    })
    def test_text_models_rejects_ollama_missing_message(self, mocked_list: mock.Mock) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run(["text-models", "--ollama-url", "http://localhost:11434", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("text models payload message must be text", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_openai_compatible_models", return_value="invalid")
    def test_text_models_rejects_non_object_openai_payload(self, mocked_list: mock.Mock) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run([
                "text-models",
                "--backend",
                "openai-compatible",
                "--openai-compatible-url",
                "http://127.0.0.1:8000/v1",
                "--json",
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("text models payload must be an object", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_openai_compatible_models", return_value={
        "available": True,
        "models": ["invalid"],
        "message": "OpenAI-compatible models loaded",
    })
    def test_text_models_rejects_openai_invalid_model_entry(self, mocked_list: mock.Mock) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run([
                "text-models",
                "--backend",
                "openai-compatible",
                "--openai-compatible-url",
                "http://127.0.0.1:8000/v1",
                "--json",
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("model payload entry must be an object", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_openai_compatible_models", return_value={
        "available": "yes",
        "models": [{"name": "local-llama"}],
        "message": "OpenAI-compatible models loaded",
    })
    def test_text_models_rejects_openai_invalid_available(self, mocked_list: mock.Mock) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run([
                "text-models",
                "--backend",
                "openai-compatible",
                "--openai-compatible-url",
                "http://127.0.0.1:8000/v1",
                "--json",
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("text models payload available must be a boolean", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_openai_compatible_models", return_value={
        "available": True,
        "models": "invalid",
        "message": "OpenAI-compatible models loaded",
    })
    def test_text_models_rejects_openai_invalid_models(self, mocked_list: mock.Mock) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run([
                "text-models",
                "--backend",
                "openai-compatible",
                "--openai-compatible-url",
                "http://127.0.0.1:8000/v1",
                "--json",
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("model payload must be a list", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_openai_compatible_models", return_value={
        "available": True,
        "models": [{"name": "local-llama"}],
        "message": "\u0000",
    })
    def test_text_models_rejects_openai_invalid_message_bytes(self, mocked_list: mock.Mock) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run([
                "text-models",
                "--backend",
                "openai-compatible",
                "--openai-compatible-url",
                "http://127.0.0.1:8000/v1",
                "--json",
        ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("text models payload message contains invalid null byte", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_openai_compatible_models", return_value={
        "available": True,
        "models": [{"name": "local-llama"}],
        "message": True,
    })
    def test_text_models_rejects_openai_invalid_message(self, mocked_list: mock.Mock) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run([
                "text-models",
                "--backend",
                "openai-compatible",
                "--openai-compatible-url",
                "http://127.0.0.1:8000/v1",
                "--json",
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("text models payload message must be text", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_openai_compatible_models", return_value={
        "available": True,
        "models": [{"name": "local-llama"}],
    })
    def test_text_models_rejects_openai_missing_message(self, mocked_list: mock.Mock) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run([
                "text-models",
                "--backend",
                "openai-compatible",
                "--openai-compatible-url",
                "http://127.0.0.1:8000/v1",
                "--json",
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("text models payload message must be text", payload["error"])

    def test_text_models_rejects_invalid_backend(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "text models backend must be ollama or openai-compatible"):
            cli.command_text_models(argparse.Namespace(
                backend="openai",
                ollama_url="http://localhost:11434",
                openai_compatible_url="http://127.0.0.1:8000/v1",
            ))

    @mock.patch("speed_of_cinnamon.cli.doctor_report")
    def test_setup_command_outputs_copyable_plan(self, mocked_doctor: mock.Mock) -> None:
        mocked_doctor.return_value = {
            "ok": False,
            "configured": {
                "recorder": {"ok": True},
                "transcriber": {
                    "ok": False,
                    "value": "auto",
                    "detail": "install whisper, install faster-whisper, configure whisper.cpp with a model, or set a custom transcriber command",
                },
                "output": {"ok": True},
                "postprocessor": {"ok": True},
                "warnings": [],
            },
            "desktop": {"cinnamon": True},
        }
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run(["setup", "--applet", "--settings-json", '{"transcriber":"auto"}', "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertFalse(payload["ready"])
        self.assertIn("Speed of Cinnamon setup plan", payload["text"])
        self.assertEqual(payload["steps"][0]["id"], "asr-backend")
        mocked_doctor.assert_called_once()

    @mock.patch("speed_of_cinnamon.cli.doctor_report")
    def test_doctor_command_rejects_non_boolean_applet(self, mocked_doctor: mock.Mock) -> None:
        with self.assertRaisesRegex(RuntimeError, "applet must be a boolean"):
            cli.command_doctor(argparse.Namespace(settings_json="{}", applet="yes"))
        mocked_doctor.assert_not_called()

    @mock.patch("speed_of_cinnamon.cli.build_setup_plan")
    @mock.patch("speed_of_cinnamon.cli.doctor_report")
    def test_setup_command_rejects_non_boolean_applet(self, mocked_doctor: mock.Mock, mocked_setup_plan: mock.Mock) -> None:
        with self.assertRaisesRegex(RuntimeError, "applet must be a boolean"):
            cli.command_setup(argparse.Namespace(settings_json="{}", applet="yes"))
        mocked_doctor.assert_not_called()
        mocked_setup_plan.assert_not_called()

    def test_alarms_check_rejects_non_boolean_mark(self) -> None:
        with mock.patch("speed_of_cinnamon.cli.ensure_runtime_dirs"):
            with self.assertRaisesRegex(RuntimeError, "mark must be a boolean"):
                cli.command_alarms_check(argparse.Namespace(catch_up_minutes=15, mark="true"))

    @mock.patch("speed_of_cinnamon.cli.add_alarm")
    def test_alarms_add_rejects_non_boolean_disabled(self, mocked_add_alarm: mock.Mock) -> None:
        with mock.patch("speed_of_cinnamon.cli.ensure_runtime_dirs"):
            with self.assertRaisesRegex(RuntimeError, "disabled must be a boolean"):
                cli.command_alarms_add(argparse.Namespace(time="09:00", name="", days="daily", urgency="normal", disabled="yes"))
            mocked_add_alarm.assert_not_called()

    def test_coerce_bool_rejects_non_boolean_values(self) -> None:
        self.assertTrue(cli._coerce_bool(True, field_name="flag"))
        self.assertFalse(cli._coerce_bool(False, field_name="flag"))
        with self.assertRaisesRegex(RuntimeError, "flag must be a boolean"):
            cli._coerce_bool("true", field_name="flag")
        with self.assertRaisesRegex(RuntimeError, "flag must be a boolean"):
            cli._coerce_bool(1, field_name="flag")

    @mock.patch("speed_of_cinnamon.cli.remove_model")
    def test_remove_model_command(self, mocked_remove: mock.Mock) -> None:
        mocked_remove.return_value = {"status": "done", "removed": True, "name": "tiny.en"}
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run(["remove-model", "tiny.en", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(payload["removed"])
        mocked_remove.assert_called_once_with("tiny.en")

    @mock.patch("speed_of_cinnamon.cli.download_model")
    def test_download_model_command_rejects_non_boolean_force(self, mocked_download: mock.Mock) -> None:
        with mock.patch("speed_of_cinnamon.cli.ensure_runtime_dirs"):
            with self.assertRaisesRegex(RuntimeError, "force must be a boolean"):
                cli.command_download_model(argparse.Namespace(model="tiny.en", force="yes"))
            mocked_download.assert_not_called()

    @mock.patch("speed_of_cinnamon.cli.insert_text")
    def test_insert_text_command_rejects_non_boolean_sanitize_special_chars(self, mocked_insert: mock.Mock) -> None:
        with self.assertRaisesRegex(RuntimeError, "sanitize_special_chars must be a boolean"):
            cli.command_insert_text(argparse.Namespace(
                text="Hello",
                insert_method="none",
                typing_delay_ms=0,
                sanitize_special_chars="yes",
            ))
        mocked_insert.assert_not_called()

    def test_settings_export_import_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            export_path = Path(tmp) / "settings.json"
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                add_alarm("09:00", name="Standup", days="weekdays")
                code = cli.run([
                    "settings-export",
                    "--settings-json",
                    json.dumps({
                        "language": "de",
                        "auto-transcribe-timeout": False,
                        "notify-complete": False,
                        "sanitize-special-chars": True,
                        "cli-path": "/tmp/local",
                    }),
                    "--output",
                    str(export_path),
                    "--json",
                ])
            export_payload = json.loads(stdout.getvalue())
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}):
                save_alarm_store({"alarms": [], "last_checked_at": ""})
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                import_code = cli.run(["settings-import", "--input", str(export_path), "--json"])
            import_payload = json.loads(stdout.getvalue())
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}):
                alarms = list_alarm_payload()
        self.assertEqual(code, 0)
        self.assertEqual(import_code, 0)
        self.assertEqual(export_payload["path"], str(export_path))
        self.assertEqual(export_payload["alarms_count"], 1)
        self.assertEqual(import_payload["alarms_count"], 1)
        self.assertEqual(import_payload["settings"]["language"], "de")
        self.assertFalse(import_payload["settings"]["auto-transcribe-timeout"])
        self.assertFalse(import_payload["settings"]["notify-complete"])
        self.assertTrue(import_payload["settings"]["sanitize-special-chars"])
        self.assertNotIn("cli-path", import_payload["settings"])
        self.assertEqual(alarms["alarms"][0]["name"], "Standup")

    def test_history_lists_recent_transcripts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcript_dir = Path(tmp) / "speed-of-cinnamon" / "transcripts"
            transcript_dir.mkdir(parents=True)
            older = transcript_dir / "older.txt"
            newer = transcript_dir / "newer.txt"
            older.write_text("older text\n", encoding="utf-8")
            newer.write_text("newer text with more words\n", encoding="utf-8")
            os.utime(older, (100, 100))
            os.utime(newer, (200, 200))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["history", "--limit", "1", "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(len(payload["transcripts"]), 1)
        self.assertEqual(payload["transcripts"][0]["name"], "newer.txt")
        self.assertEqual(payload["transcripts"][0]["text"], "newer text with more words")
        self.assertEqual(payload["transcripts"][0]["preview"], "newer text with more words")

    def test_history_skips_empty_transcripts_when_filling_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcript_dir = Path(tmp) / "speed-of-cinnamon" / "transcripts"
            transcript_dir.mkdir(parents=True)
            newest_empty = transcript_dir / "newest-empty.txt"
            older = transcript_dir / "older.txt"
            middle = transcript_dir / "middle.txt"
            newest_empty.write_text("\n", encoding="utf-8")
            older.write_text("older\n", encoding="utf-8")
            middle.write_text("middle text\n", encoding="utf-8")
            os.utime(newest_empty, (300, 300))
            os.utime(older, (100, 100))
            os.utime(middle, (200, 200))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["history", "--limit", "2", "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(len(payload["transcripts"]), 2)
        self.assertEqual(payload["transcripts"][0]["name"], "middle.txt")
        self.assertEqual(payload["transcripts"][1]["name"], "older.txt")

    def test_history_limit_zero_returns_no_transcripts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcript_dir = Path(tmp) / "speed-of-cinnamon" / "transcripts"
            transcript_dir.mkdir(parents=True)
            (transcript_dir / "entry.txt").write_text("text\n", encoding="utf-8")
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["history", "--limit", "0", "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["transcripts"], [])

    def test_history_limits_text_read_to_prevent_large_reads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcript_dir = Path(tmp) / "speed-of-cinnamon" / "transcripts"
            transcript_dir.mkdir(parents=True)
            (transcript_dir / "huge.txt").write_text("x" * 5000, encoding="utf-8")
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["history", "--limit", "1", "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(len(payload["transcripts"]), 1)
        self.assertEqual(payload["transcripts"][0]["name"], "huge.txt")
        self.assertLessEqual(len(payload["transcripts"][0]["text"]), 4000)

    def test_history_skips_symlinked_transcripts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcript_dir = Path(tmp) / "speed-of-cinnamon" / "transcripts"
            transcript_dir.mkdir(parents=True)
            real = transcript_dir / "real.txt"
            real.write_text("real transcript\n", encoding="utf-8")
            symlink = transcript_dir / "link.txt"
            symlink.symlink_to(real)
            os.utime(real, (100, 100))
            os.utime(symlink, (200, 200), follow_symlinks=False)
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["history", "--limit", "5", "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(len(payload["transcripts"]), 1)
        self.assertEqual(payload["transcripts"][0]["name"], "real.txt")

    def test_history_rejects_negative_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["history", "--limit", "-1", "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("history limit must be at least 0", payload["error"])

    def test_history_rejects_excessive_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["history", "--limit", str(cli.MAX_HISTORY_LIMIT + 1), "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("history limit must be at most", payload["error"])

    def test_cleanup_prunes_old_transcripts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcript_dir = Path(tmp) / "speed-of-cinnamon" / "transcripts"
            transcript_dir.mkdir(parents=True)
            older = transcript_dir / "older.txt"
            middle = transcript_dir / "middle.txt"
            newer = transcript_dir / "newer.txt"
            for path, mtime in [(older, 100), (middle, 200), (newer, 300)]:
                path.write_text(path.stem, encoding="utf-8")
                os.utime(path, (mtime, mtime))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["cleanup", "--keep-transcripts", "2", "--keep-recordings", "0", "--json"])
            payload = json.loads(stdout.getvalue())
            older_exists = older.exists()
            middle_exists = middle.exists()
            newer_exists = newer.exists()
        self.assertEqual(code, 0)
        self.assertEqual(payload["deleted_transcripts"], 1)
        self.assertFalse(older_exists)
        self.assertTrue(middle_exists)
        self.assertTrue(newer_exists)

    def test_cleanup_prunes_recording_groups_and_skips_active_state_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings.mkdir(parents=True)
            state_file = tmp_path / "state.json"

            def write_group(stem: str, mtime: int) -> tuple[Path, Path]:
                audio = recordings / f"{stem}.wav"
                log = recordings / f"{stem}.log"
                audio.write_bytes(b"audio")
                log.write_text("log", encoding="utf-8")
                os.utime(audio, (mtime, mtime))
                os.utime(log, (mtime, mtime))
                return audio, log

            old_audio, old_log = write_group("old", 100)
            new_audio, new_log = write_group("new", 300)
            active_audio, active_log = write_group("active", 50)
            StateStore(state_file).write(
                RecordingState(status="recording", audio_path=str(active_audio), log_path=str(active_log))
            )

            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "cleanup",
                    "--state-file",
                    str(state_file),
                    "--keep-transcripts",
                    "0",
                    "--keep-recordings",
                    "1",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
            old_audio_exists = old_audio.exists()
            old_log_exists = old_log.exists()
            new_audio_exists = new_audio.exists()
            new_log_exists = new_log.exists()
            active_audio_exists = active_audio.exists()
            active_log_exists = active_log.exists()
        self.assertEqual(code, 0)
        self.assertEqual(payload["deleted_recordings"], 1)
        self.assertEqual(payload["deleted_logs"], 1)
        self.assertFalse(old_audio_exists)
        self.assertFalse(old_log_exists)
        self.assertTrue(new_audio_exists)
        self.assertTrue(new_log_exists)
        self.assertTrue(active_audio_exists)
        self.assertTrue(active_log_exists)
        self.assertIn(str(active_audio), payload["skipped_active_paths"])
        self.assertIn(str(active_log), payload["skipped_active_paths"])

    def test_cleanup_dry_run_does_not_delete_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcript_dir = Path(tmp) / "speed-of-cinnamon" / "transcripts"
            transcript_dir.mkdir(parents=True)
            old = transcript_dir / "old.txt"
            old.write_text("old", encoding="utf-8")
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["cleanup", "--keep-transcripts", "0", "--keep-recordings", "0", "--dry-run", "--json"])
            payload = json.loads(stdout.getvalue())
            old_exists = old.exists()
        self.assertEqual(code, 0)
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["deleted_transcripts"], 0)
        self.assertEqual(payload["would_delete_transcripts"], 1)
        self.assertTrue(old_exists)

    def test_cleanup_rejects_boolean_recording_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}),
                mock.patch(
                    "speed_of_cinnamon.cli.prune_recording_groups",
                    return_value={
                        "planned_recordings": True,
                        "planned_logs": 0,
                        "planned_paths": [],
                        "deleted_recordings": 0,
                        "deleted_logs": 0,
                        "deleted_paths": [],
                        "failed_paths": [],
                        "skipped_active_paths": [],
                    },
                ),
                redirect_stdout(stdout),
            ):
                code = cli.run(["cleanup", "--keep-transcripts", "0", "--keep-recordings", "0", "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("planned-recordings must be an integer", payload["error"])

    def test_cleanup_rejects_negative_keep_transcripts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["cleanup", "--keep-transcripts", "-1", "--keep-recordings", "0", "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("keep-transcripts must be at least 0", payload["error"])

    def test_cleanup_rejects_excessive_keep_recordings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "cleanup",
                    "--keep-transcripts",
                    "0",
                    "--keep-recordings",
                    str(cli.MAX_KEEP_RECORDINGS + 1),
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("keep-recordings must be at most", payload["error"])

    def test_alarms_check_rejects_negative_catch_up_minutes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["alarms", "check", "--catch-up-minutes", "-1", "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("catch-up-minutes must be at least 0", payload["error"])

    def test_alarms_check_rejects_excessive_catch_up_minutes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "alarms",
                    "check",
                    "--catch-up-minutes",
                    str(cli.MAX_ALARM_CATCH_UP_MINUTES + 1),
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("catch-up-minutes must be at most", payload["error"])

    def test_alarms_add_rejects_null_byte_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "alarms",
                    "add",
                    "--time",
                    "09:00",
                    "--name",
                    "private\x00alarm",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("alarm name contains invalid null byte", payload["error"])

    def test_alarms_add_rejects_null_byte_days(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "alarms",
                    "add",
                    "--time",
                    "09:00",
                    "--days",
                    "mon\x00,fri",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("alarm days contains invalid null byte", payload["error"])

    def test_alarms_add_rejects_oversized_days_input(self) -> None:
        days = ",".join(["mon", "tue", "wed", "thu", "fri", "sat", "sun"] * 30)
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "alarms",
                    "add",
                    "--time",
                    "09:00",
                    "--days",
                    days,
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("alarm days is too large", payload["error"])

    def test_alarms_add_rejects_oversized_name_input(self) -> None:
        name = "A" * (MAX_ALARM_NAME_CHARS + 10)
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "alarms",
                    "add",
                    "--time",
                    "09:00",
                    "--name",
                    name,
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("alarm name is too large", payload["error"])

    def test_alarms_remove_rejects_null_byte_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["alarms", "remove", "alarm\x00id", "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("alarm id contains invalid null byte", payload["error"])

    def test_alarms_enable_rejects_null_byte_id(self) -> None:
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["alarms", "enable", "alarm\x00id", "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("alarm id contains invalid null byte", payload["error"])

    def test_alarms_enable_rejects_oversized_id(self) -> None:
        oversized_id = "X" * (MAX_ALARM_ID_CHARS + 30)
        with tempfile.TemporaryDirectory() as tmp:
            alarm_path = Path(tmp) / "speed-of-cinnamon" / "alarms.json"
            save_alarm_store(
                {
                    "version": 1,
                    "alarms": [
                        {
                            "id": oversized_id[:MAX_ALARM_ID_CHARS],
                            "hour": 9,
                            "minute": 0,
                            "days": ["mon"],
                            "enabled": False,
                            "urgency": "normal",
                        }
                    ],
                    "last_checked_at": "",
                },
                alarm_path,
            )
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["alarms", "enable", oversized_id, "--json"])
            payload = json.loads(stdout.getvalue())
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}):
                after = list_alarm_payload(alarm_path)
        self.assertEqual(code, 1)
        self.assertIn("alarm id is too large", payload["error"])
        self.assertFalse(after["alarms"][0]["enabled"])

    def test_alarms_remove_rejects_oversized_id_without_removing_matching_entry(self) -> None:
        oversized_id = "X" * (MAX_ALARM_ID_CHARS + 30)
        with tempfile.TemporaryDirectory() as tmp:
            alarm_path = Path(tmp) / "speed-of-cinnamon" / "alarms.json"
            save_alarm_store(
                {
                    "version": 1,
                    "alarms": [
                        {
                            "id": oversized_id[:MAX_ALARM_ID_CHARS],
                            "hour": 9,
                            "minute": 0,
                            "days": ["mon"],
                            "enabled": True,
                            "urgency": "normal",
                        }
                    ],
                    "last_checked_at": "",
                },
                alarm_path,
            )
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["alarms", "remove", oversized_id, "--json"])
            payload = json.loads(stdout.getvalue())
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}):
                after = list_alarm_payload(alarm_path)
        self.assertEqual(code, 1)
        self.assertIn("alarm id is too large", payload["error"])
        self.assertEqual(len(after["alarms"]), 1)

    def test_alarms_disable_rejects_oversized_id(self) -> None:
        oversized_id = "X" * (MAX_ALARM_ID_CHARS + 30)
        with tempfile.TemporaryDirectory() as tmp:
            alarm_path = Path(tmp) / "speed-of-cinnamon" / "alarms.json"
            save_alarm_store(
                {
                    "version": 1,
                    "alarms": [
                        {
                            "id": oversized_id[:MAX_ALARM_ID_CHARS],
                            "hour": 9,
                            "minute": 0,
                            "days": ["mon"],
                            "enabled": True,
                            "urgency": "normal",
                        }
                    ],
                    "last_checked_at": "",
                },
                alarm_path,
            )
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["alarms", "disable", oversized_id, "--json"])
            payload = json.loads(stdout.getvalue())
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}):
                after = list_alarm_payload(alarm_path)
        self.assertEqual(code, 1)
        self.assertIn("alarm id is too large", payload["error"])
        self.assertTrue(after["alarms"][0]["enabled"])

    def test_alarms_disable_rejects_null_byte_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["alarms", "disable", "alarm\x00id", "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("alarm id contains invalid null byte", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_input_sources")
    def test_diagnostics_omits_transcript_text(self, mocked_sources: mock.Mock) -> None:
        mocked_sources.return_value = [
            InputSource(id="1", name="alsa_input.test", description="Test Mic", default=True)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            transcript_dir = Path(tmp) / "speed-of-cinnamon" / "transcripts"
            transcript_dir.mkdir(parents=True)
            (transcript_dir / "secret.txt").write_text("secret dictated words\n", encoding="utf-8")
            state_file = Path(tmp) / "state.json"
            StateStore(state_file).write(RecordingState(status="done", transcript="secret dictated words"))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                add_alarm("09:00", name="private alarm name")
                code = cli.run(["diagnostics", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
        encoded = json.dumps(payload)
        self.assertEqual(code, 0)
        self.assertEqual(payload["app"]["id"], "speed-of-cinnamon")
        self.assertEqual(payload["inputs"]["sources"][0]["name"], "alsa_input.test")
        self.assertIn("models", payload)
        self.assertEqual(payload["alarms"]["configured"], 1)
        self.assertIn("recent_transcripts", payload)
        self.assertEqual(payload["state"]["transcript_length"], len("secret dictated words"))
        self.assertNotIn("secret dictated words", encoded)
        self.assertNotIn("private alarm name", encoded)
        self.assertNotIn("preview", encoded)
        self.assertNotIn('"text"', encoded)

    @mock.patch("speed_of_cinnamon.cli.list_alarm_payload", return_value={"alarms": [], "last_checked_at": ""})
    @mock.patch("speed_of_cinnamon.cli.list_input_sources", return_value=[])
    def test_diagnostics_rejects_non_boolean_applet(self, mocked_sources: mock.Mock, mocked_alarms: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            with (
                mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_DATA_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.ensure_runtime_dirs"),
                mock.patch("speed_of_cinnamon.cli.list_models", return_value=[]),
            ):
                with self.assertRaisesRegex(RuntimeError, "applet must be a boolean"):
                    cli.command_diagnostics(argparse.Namespace(
                        settings_json="{}",
                        applet="yes",
                        output="",
                        save=False,
                        state_file=str(state_file),
                    ))
        mocked_sources.assert_not_called()
        mocked_alarms.assert_not_called()

    @mock.patch("speed_of_cinnamon.cli.list_alarm_payload", return_value={"alarms": "invalid", "last_checked_at": ""})
    @mock.patch("speed_of_cinnamon.cli.list_input_sources", return_value=[])
    def test_diagnostics_rejects_non_list_alarms(self, mocked_sources: mock.Mock, mocked_alarms: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_DATA_HOME": tmp}):
                with redirect_stdout(io.StringIO()) as capture:
                    code = cli.run(["diagnostics", "--state-file", str(state_file), "--json"])
            payload = json.loads(capture.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("alarms entries must be a list", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_alarm_payload", return_value=[])
    @mock.patch("speed_of_cinnamon.cli.list_input_sources", return_value=[])
    @mock.patch("speed_of_cinnamon.cli.list_models", return_value=[])
    def test_diagnostics_rejects_non_object_alarm_payload(self, mocked_models: mock.Mock, mocked_sources: mock.Mock, mocked_alarms: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_DATA_HOME": tmp}):
                with redirect_stdout(io.StringIO()) as capture:
                    code = cli.run(["diagnostics", "--state-file", str(state_file), "--json"])
            payload = json.loads(capture.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("alarms payload must be an object", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_alarm_payload", return_value={"alarms": [], "last_checked_at": ""})
    @mock.patch("speed_of_cinnamon.cli.list_input_sources", return_value="invalid")
    @mock.patch("speed_of_cinnamon.cli.list_models", return_value=[])
    def test_diagnostics_rejects_non_list_input_sources(self, mocked_models: mock.Mock, mocked_sources: mock.Mock, mocked_alarms: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_DATA_HOME": tmp}):
                with redirect_stdout(io.StringIO()) as capture:
                    code = cli.run(["diagnostics", "--state-file", str(state_file), "--json"])
            payload = json.loads(capture.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["inputs"]["ok"], False)
        self.assertIn("input sources must be a list", payload["inputs"]["error"])

    @mock.patch("speed_of_cinnamon.cli.list_alarm_payload", return_value={"alarms": [], "last_checked_at": ""})
    @mock.patch("speed_of_cinnamon.cli.list_input_sources", return_value=[object()])
    @mock.patch("speed_of_cinnamon.cli.list_models", return_value=[])
    def test_diagnostics_rejects_invalid_input_source_entry(self, mocked_models: mock.Mock, mocked_sources: mock.Mock, mocked_alarms: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_DATA_HOME": tmp}):
                with redirect_stdout(io.StringIO()) as capture:
                    code = cli.run(["diagnostics", "--state-file", str(state_file), "--json"])
            payload = json.loads(capture.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["inputs"]["ok"], False)
        self.assertIn("input source id must be text", payload["inputs"]["error"])

    @mock.patch("speed_of_cinnamon.cli.list_alarm_payload", return_value={"alarms": [], "last_checked_at": ""})
    @mock.patch("speed_of_cinnamon.cli.list_input_sources", return_value=[])
    @mock.patch("speed_of_cinnamon.cli.list_models", return_value="invalid")
    def test_diagnostics_rejects_non_list_models_payload(self, mocked_models: mock.Mock, mocked_sources: mock.Mock, mocked_alarms: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_DATA_HOME": tmp}):
                with redirect_stdout(io.StringIO()) as capture:
                    code = cli.run(["diagnostics", "--state-file", str(state_file), "--json"])
            payload = json.loads(capture.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("model payload must be a list", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_alarm_payload", return_value={"alarms": [], "last_checked_at": ""})
    @mock.patch("speed_of_cinnamon.cli.list_input_sources", return_value=[])
    @mock.patch("speed_of_cinnamon.cli.list_models", return_value=["invalid"])
    def test_diagnostics_rejects_invalid_model_entry(self, mocked_models: mock.Mock, mocked_sources: mock.Mock, mocked_alarms: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_DATA_HOME": tmp}):
                with redirect_stdout(io.StringIO()) as capture:
                    code = cli.run(["diagnostics", "--state-file", str(state_file), "--json"])
            payload = json.loads(capture.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("model payload entry must be an object", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_input_sources")
    def test_diagnostics_save_writes_private_report(self, mocked_sources: mock.Mock) -> None:
        mocked_sources.return_value = [
            InputSource(id="1", name="alsa_input.test", description="Test Mic", default=True)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "diagnostics.json"
            state_file = Path(tmp) / "state.json"
            StateStore(state_file).write(RecordingState(status="done", transcript="private words"))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "diagnostics",
                    "--state-file",
                    str(state_file),
                    "--output",
                    str(output),
                    "--applet",
                    "--settings-json",
                    json.dumps({
                        "transcriber": "command",
                        "transcriber-command": "printf hidden-command-token",
                        "insert-method": "clipboard-paste",
                        "post-process-backend": "ollama",
                        "ollama-model": "llama3.2:3b",
                        "post-process-prompt": "hidden-polish-prompt",
                        "personal-context": "hidden-context-token",
                        "vocabulary": "hidden-vocabulary-token",
                    }),
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
            saved = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(code, 0)
        self.assertEqual(payload["saved_path"], str(output))
        self.assertEqual(saved["saved_path"], str(output))
        self.assertEqual(saved["state"]["transcript_length"], len("private words"))
        encoded = json.dumps(saved)
        self.assertNotIn("private words", encoded)
        self.assertNotIn("hidden-command-token", encoded)
        self.assertNotIn("hidden-polish-prompt", encoded)
        self.assertNotIn("hidden-context-token", encoded)
        self.assertNotIn("hidden-vocabulary-token", encoded)

    def test_diagnostics_save_rejects_atomic_write_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "diagnostics.json"
            state_file = Path(tmp) / "state.json"
            StateStore(state_file).write(RecordingState(status="done", transcript="private words"))
            stdout = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.os.replace", side_effect=OSError("disk full")),
                redirect_stdout(stdout),
            ):
                code = cli.run([
                    "diagnostics",
                    "--state-file",
                    str(state_file),
                    "--output",
                    str(output),
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("failed to write JSON output", payload["error"])

    def test_diagnostics_rejects_overlong_output_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["diagnostics", "--output", "x" * (cli.MAX_PATH_CHARS + 10), "--save", "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("too large", payload["error"])

    def test_diagnostics_rejects_large_settings_json(self) -> None:
        long_json = json.dumps({"payload": "x" * (cli.MAX_SETTINGS_JSON_CHARS + 10)})
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["diagnostics", "--settings-json", long_json, "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("settings JSON is too large", payload["error"])

    def test_diagnostics_rejects_non_json_output_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            output = Path(tmp) / "diagnostics.txt"
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["diagnostics", "--save", "--output", str(output), "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("must end with .json", payload["error"])

    def test_diagnostics_rejects_null_state_file_path(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run(["diagnostics", "--state-file", "state\x00.json", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("contains invalid null byte", payload["error"])

    def test_diagnostics_rejects_null_output_path(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run(["diagnostics", "--output", "output\x00.json", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("contains invalid null byte", payload["error"])

    def test_settings_export_rejects_overlong_output_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "settings-export",
                    "--settings-json",
                    '{"language":"en"}',
                    "--output",
                    "x" * (cli.MAX_PATH_CHARS + 10),
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("too large", payload["error"])

    def test_settings_export_rejects_non_object_settings_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "settings-export",
                    "--settings-json",
                    "[\"language\", \"de\"]",
                    "--output",
                    str(Path(tmp) / "settings.json"),
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("settings JSON must be an object", payload["error"])

    def test_settings_export_rejects_invalid_settings_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "settings-export",
                    "--settings-json",
                    "{\"language\": \"de\"",
                    "--output",
                    str(Path(tmp) / "settings.json"),
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("settings JSON could not be parsed", payload["error"])

    def test_settings_export_rejects_null_byte_in_settings_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "settings-export",
                    "--settings-json",
                    '{"language":"en\\u0000"}',
                    "--output",
                    str(Path(tmp) / "settings.json"),
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("contains invalid null byte", payload["error"])

    def test_settings_export_rejects_non_json_output_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "settings-export",
                    "--settings-json",
                    '{"language":"en"}',
                    "--output",
                    str(Path(tmp) / "settings.txt"),
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("must end with .json", payload["error"])

    def test_settings_import_rejects_overlong_input_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["settings-import", "--input", "x" * (cli.MAX_PATH_CHARS + 10), "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("too large", payload["error"])

    def test_settings_import_rejects_non_json_input_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["settings-import", "--input", str(Path(tmp) / "settings.txt"), "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("must end with .json", payload["error"])

    def test_settings_import_rejects_null_byte_in_export_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text('{"app":"speed-of-cinnamon","version":2,"settings":{"language":"de\\u0000"}}', encoding="utf-8")
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["settings-import", "--input", str(path), "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("invalid null byte", payload["error"])

    def test_settings_import_rejects_invalid_utf8_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_bytes(b"\xff")
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["settings-import", "--input", str(path), "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("settings export could not be read", payload["error"])

    def test_settings_import_skips_invalid_alarm_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(
                '{"app":"speed-of-cinnamon","version":2,"settings":{"language":"en"},'
                '"alarms":{"version":2,"alarms":[{"id":"good","hour":9,"minute":0,"days":["mon"],"name":"Good"},'
                '{"id":"bad","hour":"not-a-number","minute":0,"days":["mon"],"name":"Bad"}],'
                '"last_checked_at":"2026-06-01T09:00"}}',
                encoding="utf-8",
            )
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["settings-import", "--input", str(path), "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["alarms_count"], 1)
        self.assertGreater(payload["settings_count"], 0)

    def test_settings_export_rejects_null_output_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "settings-export",
                    "--settings-json",
                    '{"language":"en"}',
                    "--output",
                    "settings\x00.json",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("contains invalid null byte", payload["error"])

    def test_settings_import_rejects_null_input_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["settings-import", "--input", "settings\x00.json", "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("contains invalid null byte", payload["error"])

    def test_transcribe_file_rejects_invalid_audio_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    "x" * (cli.MAX_PATH_CHARS + 10) + ".wav",
                    "--json",
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf transcript",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("audio file path is too long", payload["error"])

    def test_transcribe_file_rejects_missing_audio_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            missing = Path(tmp) / "missing.wav"
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    str(missing),
                    "--json",
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf transcript",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("audio file is missing or empty", payload["error"])

    def test_transcribe_file_rejects_directory_as_audio_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp)
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--json",
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf transcript",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("audio path is not a regular file", payload["error"])

    def test_transcribe_file_rejects_null_audio_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    "x\x00.wav",
                    "--json",
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf transcript",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("contains invalid null byte", payload["error"])

    def test_transcribe_file_rejects_null_transcriber_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--json",
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf hi\x00",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("contains invalid null byte", payload["error"])

    def test_transcribe_file_rejects_control_character_in_transcriber_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--json",
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf hi\nwhoami",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("contains invalid control character", payload["error"])

    def test_transcribe_file_rejects_escaped_newline_in_transcriber_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--json",
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf hi\\nwhoami",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("contains invalid control character", payload["error"])

    def test_transcribe_file_rejects_overlong_personal_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--json",
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf transcript",
                    "--personal-context",
                    "x" * (cli.MAX_TRANSCRIBER_TEXT_CHARS + 10),
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("personal context is too large", payload["error"])

    def test_transcribe_file_rejects_overlong_openai_compatible_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            stdout = io.StringIO()
            long_key = "x" * (cli.MAX_OPENAI_COMPATIBLE_API_KEY_CHARS + 1)
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--json",
                    "--transcriber",
                    "openai-compatible",
                    "--openai-compatible-url",
                    "http://127.0.0.1:8000/v1",
                    "--openai-compatible-api-key",
                    long_key,
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("openai-compatible API key is too large", payload["error"])

    def test_transcribe_file_rejects_control_character_in_openai_compatible_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--json",
                    "--transcriber",
                    "openai-compatible",
                    "--openai-compatible-url",
                    "https://api.openai.com/v1",
                    "--openai-compatible-api-key",
                    "key\\nvalue",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("contains invalid control character", payload["error"])

    def test_transcribe_file_rejects_overlong_openai_compatible_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            stdout = io.StringIO()
            long_model = "x" * (cli.MAX_OPENAI_COMPATIBLE_MODEL_CHARS + 1)
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--json",
                    "--transcriber",
                    "openai-compatible",
                    "--openai-compatible-model",
                    long_model,
                    "--openai-compatible-api-key",
                    "secret",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("openai-compatible model is too large", payload["error"])

    def test_transcribe_file_rejects_overlong_openai_compatible_text_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            stdout = io.StringIO()
            long_text_model = "x" * (cli.MAX_OPENAI_COMPATIBLE_MODEL_CHARS + 1)
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--json",
                    "--transcriber",
                    "openai-compatible",
                    "--openai-compatible-model",
                    "gpt-4o-transcribe",
                    "--openai-compatible-text-model",
                    long_text_model,
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("openai-compatible text model is too large", payload["error"])

    def test_transcribe_file_rejects_non_http_openai_compatible_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--json",
                    "--transcriber",
                    "openai-compatible",
                    "--openai-compatible-url",
                    "ftp://127.0.0.1:8000/v1",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("openai-compatible url must use http:// or https://", payload["error"])

    def test_transcribe_file_rejects_openai_compatible_url_with_null_byte(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--json",
                    "--transcriber",
                    "openai-compatible",
                    "--openai-compatible-url",
                    "http://127.0.0.1:8000/v1\x00",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("contains invalid null byte", payload["error"])

    def test_stop_rejects_overlong_personal_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "processing.wav"
            audio.write_bytes(b"audio")
            state_file = Path(tmp) / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="processing", audio_path=str(audio)))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "stop",
                    "--state-file",
                    str(state_file),
                    "--insert-method",
                    "none",
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf transcript",
                    "--personal-context",
                    "x" * (cli.MAX_TRANSCRIBER_TEXT_CHARS + 10),
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
            final_state = store.read()
        self.assertEqual(code, 1)
        self.assertIn("personal context is too large", payload["error"])
        self.assertEqual(final_state.status, "processing")

    def test_stop_rejects_invalid_state_audio_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            state_file.write_text('{"status":"processing","audio_path":"x\\u0000.wav"}', encoding="utf-8")
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "stop",
                    "--state-file",
                    str(state_file),
                    "--insert-method",
                    "none",
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf transcript",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("state file could not be read", payload["error"])

    def test_remove_file_rejects_null_path(self) -> None:
        self.assertFalse(cli.remove_file("x\x00.wav", suffix=".wav"))

    def test_stop_with_invalid_pid_type_is_hardened(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "processing.wav"
            audio.write_bytes(b"audio")
            state_file = Path(tmp) / "state.json"
            state_file.write_text(
                json.dumps({
                    "status": "recording",
                    "pid": "not-an-int",
                    "audio_path": str(audio),
                }),
                encoding="utf-8",
            )
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "stop",
                    "--state-file",
                    str(state_file),
                    "--insert-method",
                    "none",
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf transcript",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("state file could not be read", payload["error"])

    def test_toggle_rejects_null_personal_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "processing.wav"
            audio.write_bytes(b"audio")
            state_file = Path(tmp) / "state.json"
            StateStore(state_file).write(RecordingState(status="recording", pid=999999999, audio_path=str(audio)))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "toggle",
                    "--state-file",
                    str(state_file),
                    "--insert-method",
                    "none",
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf transcript",
                    "--post-process-prompt",
                    "ctx\x00",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("contains invalid null byte", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.command_start")
    def test_toggle_starts_when_idle(self, mocked_start: mock.Mock) -> None:
        mocked_start.return_value = {"status": "recording"}
        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stdout(io.StringIO()):
                code = cli.run(["toggle", "--state-file", str(Path(tmp) / "state.json"), "--json"])
        self.assertEqual(code, 0)
        mocked_start.assert_called_once()

    def test_toggle_finalizes_expired_recording(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            audio = tmp_path / "expired.wav"
            audio.write_bytes(b"audio")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="recording", pid=999999999, audio_path=str(audio)))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "toggle",
                    "--state-file",
                    str(state_file),
                    "--insert-method",
                    "none",
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf expired-transcript",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
            final_state = store.read()
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "done")
        self.assertEqual(payload["transcript"], "expired-transcript")
        self.assertEqual(final_state.status, "done")
        self.assertEqual(final_state.transcript, "expired-transcript")

    @mock.patch("speed_of_cinnamon.cli.transcribe", return_value="ok")
    def test_toggle_accepts_transcriber_alias_openai(self, mocked_transcribe: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            audio = tmp_path / "expired.wav"
            audio.write_bytes(b"audio")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(
                RecordingState(
                    status="recording",
                    pid=123456789,
                    audio_path=str(audio),
                    language="en",
                )
            )
            with mock.patch("speed_of_cinnamon.cli.remove_file", return_value=False):
                stdout = io.StringIO()
                with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                    code = cli.run([
                        "toggle",
                        "--state-file",
                        str(state_file),
                        "--insert-method",
                        "none",
                        "--transcriber",
                        "openai",
                        "--json",
                    ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "done")
        mocked_transcribe.assert_called_once_with(
            audio_path=audio,
            language="en",
            text_path=mock.ANY,
            command_template="",
            backend="whisper",
            whisper_model="",
            personal_context="",
            vocabulary="",
        )

    @mock.patch("speed_of_cinnamon.cli.transcribe", return_value="ok")
    def test_toggle_accepts_transcriber_alias_faster_whisper(self, mocked_transcribe: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            audio = tmp_path / "expired.wav"
            audio.write_bytes(b"audio")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(
                RecordingState(
                    status="recording",
                    pid=123456789,
                    audio_path=str(audio),
                    language="en",
                )
            )
            with mock.patch("speed_of_cinnamon.cli.remove_file", return_value=False):
                stdout = io.StringIO()
                with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                    code = cli.run([
                        "toggle",
                        "--state-file",
                        str(state_file),
                        "--insert-method",
                        "none",
                        "--transcriber",
                        "faster-whisper",
                        "--json",
                    ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "done")
        mocked_transcribe.assert_called_once_with(
            audio_path=audio,
            language="en",
            text_path=mock.ANY,
            command_template="",
            backend="faster-whisper",
            whisper_model="",
            personal_context="",
            vocabulary="",
        )

    def test_toggle_finalizes_recording_with_saved_language(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            audio = tmp_path / "expired.wav"
            audio.write_bytes(b"audio")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="recording", pid=999999999, audio_path=str(audio), language="de"))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "toggle",
                    "--state-file",
                    str(state_file),
                    "--insert-method",
                    "none",
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf gespeicherte-sprache",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
            final_state = store.read()
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "done")
        self.assertEqual(payload["language"], "de")
        self.assertEqual(final_state.language, "de")

    def test_finalize_discards_recording_artifacts_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "recording.wav"
            log = recordings_root / "recording.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="processing", audio_path=str(audio), log_path=str(log)))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "stop",
                    "--state-file",
                    str(state_file),
                    "--insert-method",
                    "none",
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf private-transcript",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
            final_state = store.read()
            audio_exists = audio.exists()
            log_exists = log.exists()
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "done")
        self.assertFalse(payload["recording_artifacts_kept"])
        self.assertTrue(payload["audio_deleted"])
        self.assertTrue(payload["log_deleted"])
        self.assertFalse(audio_exists)
        self.assertFalse(log_exists)
        self.assertEqual(final_state.audio_path, "")
        self.assertEqual(final_state.log_path, "")

    def test_finalize_can_keep_recording_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "recording.wav"
            log = recordings_root / "recording.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="processing", audio_path=str(audio), log_path=str(log)))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "stop",
                    "--state-file",
                    str(state_file),
                    "--insert-method",
                    "none",
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf retained-transcript",
                    "--keep-recording-artifacts",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
            final_state = store.read()
            audio_exists = audio.exists()
            log_exists = log.exists()
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "done")
        self.assertTrue(payload["recording_artifacts_kept"])
        self.assertFalse(payload["audio_deleted"])
        self.assertFalse(payload["log_deleted"])
        self.assertTrue(audio_exists)
        self.assertTrue(log_exists)
        self.assertEqual(final_state.audio_path, str(audio))
        self.assertEqual(final_state.log_path, str(log))

    def test_finalize_rejects_non_boolean_keep_recording_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "recording.wav"
            log = recordings_root / "recording.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="processing", audio_path=str(audio), log_path=str(log)))
            args = argparse.Namespace(
                language="en",
                transcriber="command",
                transcriber_command="printf transcript",
                whisper_model="",
                post_process_command="",
                post_process_backend="command",
                post_process_prompt="",
                openai_compatible_url=cli.DEFAULT_OPENAI_COMPATIBLE_URL,
                ollama_url=cli.DEFAULT_OLLAMA_URL,
                ollama_model="",
                openai_compatible_model="",
                personal_context="",
                vocabulary="",
                append_space=False,
                sanitize_special_chars=False,
                typing_delay_ms=0,
                insert_method="none",
                keep_recording_artifacts="true",
            )
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp, "XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.transcribe", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.post_process_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.prepare_output_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.insert_text", return_value=True),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=audio),
            ):
                with self.assertRaises(RuntimeError) as context:
                    cli.finalize_recording(args, store, store.read())
                self.assertIn("must be a boolean", str(context.exception))
            final_state = store.read()
            self.assertEqual(final_state.status, "processing")
            self.assertTrue(audio.exists())
            self.assertTrue(log.exists())

    def _build_finalize_args(
        self,
        *,
        keep_recording_artifacts: bool | str = True,
        append_space: bool = False,
        sanitize_special_chars: bool = False,
    ) -> argparse.Namespace:
        return argparse.Namespace(
            language="en",
            transcriber="command",
            transcriber_command="printf transcript",
            whisper_model="",
            post_process_command="",
            post_process_backend="command",
            post_process_prompt="",
            openai_compatible_url=cli.DEFAULT_OPENAI_COMPATIBLE_URL,
            ollama_url=cli.DEFAULT_OLLAMA_URL,
            ollama_model="",
            openai_compatible_model="",
            personal_context="",
            vocabulary="",
            append_space=append_space,
            sanitize_special_chars=sanitize_special_chars,
            typing_delay_ms=0,
            insert_method="none",
            keep_recording_artifacts=keep_recording_artifacts,
        )

    def test_finalize_rejects_non_boolean_append_space(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "recording.wav"
            log = recordings_root / "recording.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="processing", audio_path=str(audio), log_path=str(log)))
            args = self._build_finalize_args(append_space="yes")
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp, "XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.transcribe", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.post_process_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.prepare_output_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.insert_text", return_value=True),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=audio),
            ):
                with self.assertRaisesRegex(RuntimeError, "append_space must be a boolean"):
                    cli.finalize_recording(args, store, store.read())

    def test_finalize_rejects_non_boolean_sanitize_special_chars(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "recording.wav"
            log = recordings_root / "recording.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="processing", audio_path=str(audio), log_path=str(log)))
            args = self._build_finalize_args(sanitize_special_chars="yes")
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp, "XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.transcribe", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.post_process_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.prepare_output_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.insert_text", return_value=True),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=audio),
            ):
                with self.assertRaisesRegex(RuntimeError, "sanitize_special_chars must be a boolean"):
                    cli.finalize_recording(args, store, store.read())

    def test_cancel_does_not_delete_artifacts_outside_recordings_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            audio = tmp_path / "outside.wav"
            log = tmp_path / "outside.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="recording", pid=999999999, audio_path=str(audio), log_path=str(log)))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["cancel", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
            audio_exists = audio.exists()
            log_exists = log.exists()
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "idle")
        self.assertEqual(payload["message"], "recording discarded")
        self.assertFalse(payload["audio_deleted"])
        self.assertFalse(payload["log_deleted"])
        self.assertTrue(audio_exists)
        self.assertTrue(log_exists)

    def test_start_does_not_overwrite_expired_recording(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            audio = tmp_path / "expired.wav"
            audio.write_bytes(b"audio")
            state_file = tmp_path / "state.json"
            StateStore(state_file).write(RecordingState(status="recording", pid=999999999, audio_path=str(audio)))
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = cli.run(["start", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "recorded")
        self.assertEqual(payload["audio_path"], str(audio))

    def test_status_includes_microphone_level_for_recording_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings.mkdir(parents=True)
            audio = recordings / "active.wav"
            self._write_wav(audio, [0, 8192, -16384])
            state_file = tmp_path / "state.json"
            StateStore(state_file).write(RecordingState(status="recording", pid=999999999, audio_path=str(audio)))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["status", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())

        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "recorded")
        self.assertEqual(payload["microphone_level"]["percent"], 50)
        self.assertEqual(payload["microphone_level"]["source"], "recording-file")

    def test_start_defaults_language_to_english(self) -> None:
        proc = mock.Mock()
        proc.pid = 12345
        proc.poll.return_value = None
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            stdout = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.choose_recorder", return_value=RecorderCommand("test-recorder", [])),
                mock.patch("speed_of_cinnamon.cli.start_recorder", return_value=proc),
                redirect_stdout(stdout),
            ):
                code = cli.run(["start", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
            state = StateStore(state_file).read()
        self.assertEqual(code, 0)
        self.assertEqual(payload["language"], "en")
        self.assertEqual(state.language, "en")

    def test_start_prepares_audio_artifact_with_private_permissions(self) -> None:
        proc = mock.Mock()
        proc.pid = 23456
        proc.poll.return_value = None
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            stdout = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.choose_recorder", return_value=RecorderCommand("test-recorder", [])),
                mock.patch("speed_of_cinnamon.cli.start_recorder", return_value=proc),
                redirect_stdout(stdout),
            ):
                code = cli.run(["start", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
            audio_path = Path(payload["audio_path"])
            mode = audio_path.stat().st_mode & 0o777
        self.assertEqual(code, 0)
        self.assertEqual(mode, 0o600)

    def test_start_auto_falls_back_when_first_recorder_exits_immediately(self) -> None:
        failed_proc = mock.Mock()
        failed_proc.pid = 23456
        failed_proc.poll.return_value = 1
        failed_proc.returncode = 1
        working_proc = mock.Mock()
        working_proc.pid = 23457
        working_proc.poll.return_value = None
        second_log_existed: list[bool] = []

        def fake_choose(preference: str, *_args: object) -> RecorderCommand:
            return RecorderCommand(preference, [preference])

        def fake_start(command: RecorderCommand, log_path: Path) -> object:
            if command.name == "pw-record":
                log_path.write_text("first recorder failed\n", encoding="utf-8")
                return failed_proc
            second_log_existed.append(log_path.exists())
            return working_proc

        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            stdout = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.choose_recorder", side_effect=fake_choose) as mocked_choose,
                mock.patch("speed_of_cinnamon.cli.start_recorder", side_effect=fake_start),
                redirect_stdout(stdout),
            ):
                code = cli.run(["start", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
            state = StateStore(state_file).read()

        self.assertEqual(code, 0)
        self.assertEqual(payload["recorder"], "parecord")
        self.assertEqual(state.recorder, "parecord")
        self.assertEqual([call.args[0] for call in mocked_choose.call_args_list], ["pw-record", "parecord"])
        self.assertEqual(second_log_existed, [False])

    def test_start_explicit_recorder_reports_immediate_exit_without_fallback(self) -> None:
        failed_proc = mock.Mock()
        failed_proc.pid = 23456
        failed_proc.poll.return_value = 1
        failed_proc.returncode = 1

        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            stdout = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}),
                mock.patch(
                    "speed_of_cinnamon.cli.choose_recorder",
                    return_value=RecorderCommand("pw-record", ["pw-record"]),
                ) as mocked_choose,
                mock.patch("speed_of_cinnamon.cli.start_recorder", return_value=failed_proc),
                redirect_stdout(stdout),
            ):
                code = cli.run(["start", "--recorder", "pw-record", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
            recording_artifacts = list((Path(tmp) / "speed-of-cinnamon" / "recordings").glob("*"))

        self.assertEqual(code, 1)
        self.assertIn("pw-record exited immediately", payload["error"])
        self.assertEqual([call.args[0] for call in mocked_choose.call_args_list], ["pw-record"])
        self.assertEqual(recording_artifacts, [])

    def test_start_auto_removes_artifacts_when_all_recorders_fail(self) -> None:
        failed_processes = []
        for returncode in (1, 2, 3):
            proc = mock.Mock()
            proc.pid = 23456 + returncode
            proc.poll.return_value = returncode
            proc.returncode = returncode
            failed_processes.append(proc)

        def fake_choose(preference: str, *_args: object) -> RecorderCommand:
            return RecorderCommand(preference, [preference])

        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            stdout = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.choose_recorder", side_effect=fake_choose),
                mock.patch("speed_of_cinnamon.cli.start_recorder", side_effect=failed_processes),
                redirect_stdout(stdout),
            ):
                code = cli.run(["start", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
            recording_artifacts = list((Path(tmp) / "speed-of-cinnamon" / "recordings").glob("*"))

        self.assertEqual(code, 1)
        self.assertIn("no recorder backend started successfully", payload["error"])
        self.assertEqual(recording_artifacts, [])

    def test_start_rejects_negative_max_seconds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["start", "--max-seconds", "-1", "--state-file", str(Path(tmp) / "state.json"), "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("max-seconds must be at least 0", payload["error"])

    def test_start_rejects_excessive_max_seconds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "start",
                    "--max-seconds",
                    str(cli.MAX_RECORDING_SECONDS + 1),
                    "--state-file",
                    str(Path(tmp) / "state.json"),
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("max-seconds must be at most", payload["error"])

    def test_start_rejects_escaped_null_in_state_file_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["start", "--state-file", "state\\x00.json", "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("contains invalid null byte", payload["error"])

    def test_start_rejects_null_byte_input_device(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "start",
                    "--input-device",
                    "alsa\x00bad",
                    "--state-file",
                    str(Path(tmp) / "state.json"),
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("recording input device contains invalid null byte", payload["error"])

    def test_cancel_recorded_discards_files_and_resets_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "recorded.wav"
            log = recordings_root / "recorded.log"
            audio.write_bytes(b"audio")
            log.write_text("log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="recorded", audio_path=str(audio), log_path=str(log)))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["cancel", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
            final_state = store.read()
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "idle")
        self.assertEqual(payload["message"], "recording discarded")
        self.assertTrue(payload["audio_deleted"])
        self.assertTrue(payload["log_deleted"])
        self.assertFalse(audio.exists())
        self.assertFalse(log.exists())
        self.assertEqual(final_state.status, "idle")
        self.assertEqual(final_state.audio_path, "")

    @mock.patch("speed_of_cinnamon.cli.stop_process")
    @mock.patch("speed_of_cinnamon.cli.process_is_alive", return_value=True)
    def test_cancel_running_recording_stops_process(self, mocked_alive: mock.Mock, mocked_stop: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            audio = tmp_path / "recording.wav"
            audio.write_bytes(b"audio")
            state_file = tmp_path / "state.json"
            StateStore(state_file).write(RecordingState(status="recording", pid=1234, audio_path=str(audio)))
            with redirect_stdout(io.StringIO()):
                code = cli.run(["cancel", "--state-file", str(state_file), "--json"])
        self.assertEqual(code, 0)
        mocked_alive.assert_called_once_with(1234)
        mocked_stop.assert_called_once_with(1234)

    def test_finalize_error_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="processing", audio_path=str(tmp_path / "missing.wav")))
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(io.StringIO()):
                code = cli.run(["stop", "--state-file", str(state_file), "--insert-method", "none", "--json"])
            final_state = store.read()
        self.assertEqual(code, 1)
        self.assertEqual(final_state.status, "error")
        self.assertIn("missing or empty", final_state.error)

    def test_finalize_rejects_transcript_write_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "recording.wav"
            log = recordings_root / "recording.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="processing", audio_path=str(audio), log_path=str(log)))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}), mock.patch(
                "speed_of_cinnamon.cli._write_text_atomic",
                side_effect=RuntimeError("failed to write transcript file: /tmp/transcript.txt"),
            ), redirect_stdout(stdout):
                code = cli.run([
                    "stop",
                    "--state-file",
                    str(state_file),
                    "--insert-method",
                    "none",
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf finalize-transcript",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
            final_state = store.read()
        self.assertEqual(code, 1)
        self.assertEqual(final_state.status, "error")
        self.assertIn("failed to write transcript file", payload["error"])
        self.assertIn("failed to write transcript file", final_state.error)

    def test_read_file_tail_rejects_invalid_utf8(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.txt"
            path.write_bytes(b"bad\xff")
            with self.assertRaisesRegex(ValueError, "failed to decode file as UTF-8"):
                cli.read_file_tail(path, 10)

    def test_read_file_tail_opens_without_following_symlinks(self) -> None:
        captured: dict[str, object] = {}
        handle = mock.Mock()
        handle.read.return_value = b"hello"
        handle.tell.return_value = 5

        def fake_os_open(path: Path, flags: int, mode: int = 0o600) -> int:
            captured["path"] = path
            captured["flags"] = flags
            captured["mode"] = mode
            return 11

        with (
            mock.patch("speed_of_cinnamon.cli.os.open", side_effect=fake_os_open),
            mock.patch("speed_of_cinnamon.cli.os.fdopen", return_value=handle),
        ):
            text = cli.read_file_tail(Path("/tmp/sample.txt"), 10)

        self.assertEqual(text, "hello")
        self.assertEqual(captured["path"], Path("/tmp/sample.txt"))
        self.assertEqual(captured["flags"], os.O_RDONLY | os.O_NOFOLLOW)
        handle.close.assert_called_once()

    @mock.patch("speed_of_cinnamon.cli.os.open", wraps=os.open)
    def test_prepare_private_file_uses_secure_open_flags(self, mocked_open: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "recording.wav"
            cli._prepare_private_file(path, field_name="recording audio file")

        self.assertTrue(
            any(
                Path(args[0]) == path and isinstance(args[1], int) and args[1] & os.O_NOFOLLOW
                for args, _ in mocked_open.call_args_list
            )
        )

    def test_read_file_tail_rejects_escaped_null(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.txt"
            path.write_text("line\\x00end", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "contains invalid null byte"):
                cli.read_file_tail(path, 10)

    def test_read_file_tail_rejects_request_exceeding_history_cap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.txt"
            path.write_text("ok", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "max_chars must be at most"):
                cli.read_file_tail(path, cli.MAX_TRANSCRIPT_HISTORY_TEXT_CHARS + 1)

    def test_read_log_excerpt_rejects_request_exceeding_log_cap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.txt"
            path.write_text("ok", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "max_chars must be at most"):
                cli.read_log_excerpt(path, cli.MAX_LOG_EXCERPT_CHARS + 1)

    def test_coerce_int_rejects_bool(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "must be an integer"):
            cli._coerce_int(True, field_name="max")

    def test_coerce_int_rejects_float(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "must be an integer"):
            cli._coerce_int(1.0, field_name="max")  # type: ignore[arg-type]

    def test_assert_clean_text_rejects_non_text_value(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "must be text"):
            cli._assert_clean_text(123, field_name="value", max_chars=10)  # type: ignore[arg-type]

    def test_assert_clean_text_rejects_control_characters(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "contains invalid control character"):
            cli._assert_clean_text("line1\nline2", field_name="value", max_chars=20)
        with self.assertRaisesRegex(RuntimeError, "contains invalid control character"):
            cli._assert_clean_text("line1\\nline2", field_name="value", max_chars=20)
        with self.assertRaisesRegex(RuntimeError, "contains invalid control character"):
            cli._assert_clean_text("line1\\rline2", field_name="value", max_chars=20)
        with self.assertRaisesRegex(RuntimeError, "contains invalid control character"):
            cli._assert_clean_text("line1\\x0a", field_name="value", max_chars=20)
        with self.assertRaisesRegex(RuntimeError, "contains invalid control character"):
            cli._assert_clean_text("line1\\x0d", field_name="value", max_chars=20)
        with self.assertRaisesRegex(RuntimeError, "contains invalid control character"):
            cli._assert_clean_text("line1\\u000a", field_name="value", max_chars=20)

    def test_assert_text_limit_rejects_oversized_bytes(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "is too large"):
            cli._assert_text_limit("😀😀", field_name="value", max_chars=4)

    def test_coerce_path_rejects_non_text_value(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "must be text"):
            cli._coerce_path(123, field_name="path")  # type: ignore[arg-type]

    def test_read_file_tail_rejects_nonpositive_max_chars(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.txt"
            path.write_text("ok", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "max_chars must be positive"):
                cli.read_file_tail(path, 0)

    def test_read_file_tail_rejects_invalid_path_type(self) -> None:
        with self.assertRaisesRegex(TypeError, "path must be a Path"):
            cli.read_file_tail(123, 10)  # type: ignore[arg-type]

    def test_read_log_excerpt_rejects_invalid_path_type(self) -> None:
        with self.assertRaisesRegex(TypeError, "path must be a Path"):
            cli.read_log_excerpt(123, 10)  # type: ignore[arg-type]

    def test_read_file_tail_rejects_non_integer_max_chars(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.txt"
            path.write_text("ok", encoding="utf-8")
            with self.assertRaisesRegex(TypeError, "max_chars must be an integer"):
                cli.read_file_tail(path, 1.5)  # type: ignore[arg-type]

    def test_parse_cli_settings_json_rejects_non_text(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "must be text"):
            cli._parse_cli_settings_json({} )  # type: ignore[arg-type]

    def test_parse_cli_settings_json_rejects_overlong_bytes(self) -> None:
        raw = json.dumps({"payload": "😀" * ((cli.MAX_SETTINGS_JSON_CHARS // 4) + 1)})
        with self.assertRaisesRegex(RuntimeError, "too large"):
            cli._parse_cli_settings_json(raw)

    def test_contains_escaped_null_rejects_non_text(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "value must be text"):
            cli._contains_escaped_null(12)  # type: ignore[arg-type]

    def test_contains_escaped_null_rejects_bool(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "value must be text"):
            cli._contains_escaped_null(True)  # type: ignore[arg-type]

    def test_append_space_if_needed_rejects_non_text(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "text must be text"):
            cli.append_space_if_needed(123, True)  # type: ignore[arg-type]

    def test_append_space_if_needed_rejects_non_bool_flag(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "append_space must be a boolean"):
            cli.append_space_if_needed("hello", "yes")  # type: ignore[arg-type]

    def test_prepare_output_text_rejects_non_bool_sanitize_flag(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "sanitize must be a boolean"):
            cli.prepare_output_text("hello", True, "yes")  # type: ignore[arg-type]

if __name__ == "__main__":
    unittest.main()
