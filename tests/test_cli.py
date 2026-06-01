from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from speed_of_cinnamon import cli
from speed_of_cinnamon.alarms import add_alarm, list_alarm_payload, save_alarm_store
from speed_of_cinnamon.recorder import InputSource, RecorderCommand
from speed_of_cinnamon.state import RecordingState, StateStore


class CliTest(unittest.TestCase):
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

    @mock.patch("speed_of_cinnamon.cli.list_ollama_models")
    def test_text_models_lists_local_ollama_models(self, mocked_list: mock.Mock) -> None:
        mocked_list.return_value = {
            "available": True,
            "models": [{"name": "llama3.2:3b"}],
            "message": "Ollama models loaded",
        }
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run(["text-models", "--ollama-url", "http://localhost:11434", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["backend"], "ollama")
        self.assertEqual(payload["url"], "http://localhost:11434")
        self.assertEqual(payload["models"][0]["name"], "llama3.2:3b")
        mocked_list.assert_called_once_with("http://localhost:11434")

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

    @mock.patch("speed_of_cinnamon.cli.list_openai_compatible_models")
    def test_text_models_lists_openai_compatible_local_models(self, mocked_list: mock.Mock) -> None:
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
                "--json",
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["backend"], "openai-compatible")
        self.assertEqual(payload["url"], "http://127.0.0.1:8000/v1")
        self.assertEqual(payload["models"][0]["name"], "local-llama")
        mocked_list.assert_called_once_with("http://127.0.0.1:8000/v1")

    @mock.patch("speed_of_cinnamon.cli.doctor_report")
    def test_setup_command_outputs_copyable_plan(self, mocked_doctor: mock.Mock) -> None:
        mocked_doctor.return_value = {
            "ok": False,
            "configured": {
                "recorder": {"ok": True},
                "transcriber": {
                    "ok": False,
                    "value": "auto",
                    "detail": "install whisper, configure whisper.cpp with a model, or set a custom transcriber command",
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
            store = StateStore(state_file)
            store.write(RecordingState(status="processing", audio_path="x\x00.wav"))
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
            final_state = store.read()
        self.assertEqual(code, 1)
        self.assertIn("recording audio path is invalid", payload["error"])
        self.assertEqual(final_state.status, "error")

    def test_remove_file_rejects_null_path(self) -> None:
        self.assertFalse(cli.remove_file("x\x00.wav", suffix=".wav"))

    def test_stop_with_invalid_pid_type_is_hardened(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "processing.wav"
            audio.write_bytes(b"audio")
            state_file = Path(tmp) / "state.json"
            state = StateStore(state_file)
            state.write(
                RecordingState(
                    status="recording",
                    pid="not-an-int",  # type: ignore[arg-type]
                    audio_path=str(audio),
                )
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
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "done")
        self.assertEqual(payload["transcript"], "transcript")

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
        self.assertIsNone(final_state.audio_path)
        self.assertIsNone(final_state.log_path)

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
        self.assertIsNone(final_state.audio_path)

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



if __name__ == "__main__":
    unittest.main()
