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
