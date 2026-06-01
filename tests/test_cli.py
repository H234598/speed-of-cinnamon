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
from speed_of_cinnamon.recorder import InputSource
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

    def test_settings_export_import_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            export_path = Path(tmp) / "settings.json"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
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
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                import_code = cli.run(["settings-import", "--input", str(export_path), "--json"])
            import_payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(import_code, 0)
        self.assertEqual(export_payload["path"], str(export_path))
        self.assertEqual(import_payload["settings"]["language"], "de")
        self.assertFalse(import_payload["settings"]["auto-transcribe-timeout"])
        self.assertFalse(import_payload["settings"]["notify-complete"])
        self.assertTrue(import_payload["settings"]["sanitize-special-chars"])
        self.assertNotIn("cli-path", import_payload["settings"])

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
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["diagnostics", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
        encoded = json.dumps(payload)
        self.assertEqual(code, 0)
        self.assertEqual(payload["app"]["id"], "speed-of-cinnamon")
        self.assertEqual(payload["inputs"]["sources"][0]["name"], "alsa_input.test")
        self.assertIn("recent_transcripts", payload)
        self.assertEqual(payload["state"]["transcript_length"], len("secret dictated words"))
        self.assertNotIn("secret dictated words", encoded)
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
                    }),
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
            saved = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(code, 0)
        self.assertEqual(payload["saved_path"], str(output))
        self.assertEqual(saved["saved_path"], str(output))
        self.assertEqual(saved["state"]["transcript_length"], len("private words"))
        self.assertNotIn("private words", json.dumps(saved))
        self.assertNotIn("hidden-command-token", json.dumps(saved))

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

    def test_cancel_recorded_discards_files_and_resets_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            audio = tmp_path / "recorded.wav"
            log = tmp_path / "recorded.log"
            audio.write_bytes(b"audio")
            log.write_text("log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="recorded", audio_path=str(audio), log_path=str(log)))
            stdout = io.StringIO()
            with redirect_stdout(stdout):
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


if __name__ == "__main__":
    unittest.main()
