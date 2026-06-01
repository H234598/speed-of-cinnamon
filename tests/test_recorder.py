from __future__ import annotations

import subprocess
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from speed_of_cinnamon.recorder import (
    RecorderCommand,
    RecorderError,
    choose_recorder,
    _run_kill,
    _run_pactl_command,
    normalize_input_device,
    list_input_sources,
    parse_pactl_sources,
    start_recorder,
    stop_process,
    validate_recording_path,
)


PACTL_SOURCES = """Source #10
\tState: SUSPENDED
\tName: alsa_output.pci-speakers.monitor
\tDescription: Monitor of Speakers
\tDriver: PipeWire
\tMonitor of Sink: alsa_output.pci-speakers

Source #11
\tState: RUNNING
\tName: alsa_input.usb-mic.analog-stereo
\tDescription: USB Microphone
\tDriver: PipeWire
\tMonitor of Sink: n/a
"""


def which_only(command: str) -> mock.Mock:
    return mock.Mock(side_effect=lambda name: f"/usr/bin/{command}" if name == command else None)


class RecorderTest(unittest.TestCase):
    def test_default_input_device_is_normalized_to_empty(self) -> None:
        self.assertEqual(normalize_input_device(""), "")
        self.assertEqual(normalize_input_device("default"), "")
        self.assertEqual(normalize_input_device("@DEFAULT_SOURCE@"), "")
        self.assertEqual(normalize_input_device("alsa_input.usb-mic"), "alsa_input.usb-mic")

    def test_choose_pw_record_adds_target_before_output_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio_path = Path(tmp) / "sample.wav"
            with mock.patch("speed_of_cinnamon.recorder.shutil.which", which_only("pw-record")):
                command = choose_recorder("pw-record", audio_path, 3, "alsa_input.usb-mic")
        self.assertEqual(command.name, "pw-record")
        self.assertIn("--target", command.argv)
        self.assertEqual(command.argv[command.argv.index("--target") + 1], "alsa_input.usb-mic")
        self.assertEqual(command.argv[-1], str(audio_path))

    def test_choose_parecord_adds_device_before_output_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio_path = Path(tmp) / "sample.wav"
            with mock.patch("speed_of_cinnamon.recorder.shutil.which", which_only("parecord")):
                command = choose_recorder("parecord", audio_path, 3, "alsa_input.usb-mic")
        self.assertEqual(command.name, "parecord")
        self.assertIn("--device=alsa_input.usb-mic", command.argv)
        self.assertEqual(command.argv[-1], str(audio_path))

    def test_choose_recorder_rejects_excessive_max_seconds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio_path = Path(tmp) / "sample.wav"
            with mock.patch("speed_of_cinnamon.recorder.shutil.which", which_only("pw-record")):
                with self.assertRaisesRegex(RecorderError, "exceeds limit"):
                    choose_recorder("pw-record", audio_path, 3_601)

    def test_normalize_input_device_rejects_overly_long_name(self) -> None:
        with self.assertRaisesRegex(RecorderError, "input device name is too long"):
            normalize_input_device("x" * 300)

    def test_validate_recording_path_rejects_wrong_extension(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}):
                with self.assertRaises(RecorderError):
                    validate_recording_path(Path(tmp) / "sample.txt", suffix=".wav")

    def test_validate_recording_path_can_require_recordings_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}):
                recordings_root = Path(tmp) / "speed-of-cinnamon" / "recordings"
                valid = validate_recording_path(recordings_root / "sample.wav", suffix=".wav", require_recordings_dir=True)
            self.assertEqual(valid.name, "sample.wav")
            self.assertEqual(valid.suffix, ".wav")
            with self.assertRaises(RecorderError):
                validate_recording_path(Path(tmp) / "outside.wav", suffix=".wav", require_recordings_dir=True)

    def test_start_recorder_rejects_invalid_log_suffix(self) -> None:
        command = RecorderCommand(name="noop", argv=["true"])
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}):
                with self.assertRaises(RecorderError):
                    start_recorder(command, Path(tmp) / "session.txt")

    def test_start_recorder_rejects_empty_executable(self) -> None:
        command = RecorderCommand(name="noop", argv=[""])
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}):
                with self.assertRaisesRegex(RecorderError, "recorder executable is empty"):
                    start_recorder(command, Path(tmp) / "session.log")

    def test_start_recorder_rejects_empty_command_name(self) -> None:
        command = RecorderCommand(name=" ", argv=[""])
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}):
                with self.assertRaisesRegex(RecorderError, "recorder name is required"):
                    start_recorder(command, Path(tmp) / "session.log")

    def test_start_recorder_rejects_null_bytes(self) -> None:
        command = RecorderCommand(name="noop", argv=["true", "ok\x00"])
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}):
                with self.assertRaisesRegex(RecorderError, "recorder command contains invalid null byte"):
                    start_recorder(command, Path(tmp) / "session.log")

    def test_run_pactl_command_rejects_empty_command(self) -> None:
        with self.assertRaisesRegex(RecorderError, "empty pactl command"):
            _run_pactl_command([], required=True)

    def test_run_pactl_command_rejects_empty_executable(self) -> None:
        with self.assertRaisesRegex(RecorderError, "empty pactl executable"):
            _run_pactl_command([""], required=True)

    def test_run_pactl_command_rejects_null_bytes(self) -> None:
        with self.assertRaisesRegex(RecorderError, "pactl command contains invalid null byte"):
            _run_pactl_command(["pactl", "--name\x00"], required=True)

    def test_run_kill_rejects_null_bytes(self) -> None:
        with self.assertRaisesRegex(RecorderError, "kill command contains invalid null byte"):
            _run_kill(["kill", "-9", "\x00bad"], check_exit=False)

    def test_parse_pactl_sources_filters_monitors_and_marks_default(self) -> None:
        sources = parse_pactl_sources(PACTL_SOURCES, "alsa_input.usb-mic.analog-stereo")
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].id, "11")
        self.assertEqual(sources[0].name, "alsa_input.usb-mic.analog-stereo")
        self.assertEqual(sources[0].description, "USB Microphone")
        self.assertTrue(sources[0].default)
        self.assertFalse(sources[0].monitor)

    def test_parse_pactl_sources_can_include_monitors(self) -> None:
        sources = parse_pactl_sources(PACTL_SOURCES, include_monitors=True)
        self.assertEqual([source.id for source in sources], ["10", "11"])
        self.assertTrue(sources[0].monitor)

    def test_list_input_sources_rejects_too_large_pactl_output(self) -> None:
        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            stdout_file = kwargs["stdout"]
            command = kwargs["args"]
            if command == ["pactl", "get-default-source"]:
                stdout_file.write(b"default\n")
            else:
                stdout_file.write(("x" * (1_000_001)).encode("utf-8"))
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with (
            mock.patch("speed_of_cinnamon.recorder.shutil.which", return_value="/usr/bin/pactl"),
            mock.patch("speed_of_cinnamon.recorder.subprocess.run", side_effect=fake_run),
        ):
            with self.assertRaisesRegex(RecorderError, "output exceeded"):
                list_input_sources()

    def test_stop_process_rejects_invalid_pid(self) -> None:
        with self.assertRaisesRegex(RecorderError, "invalid process id"):
            stop_process(0)

    def test_stop_process_rejects_non_positive_timeout(self) -> None:
        with self.assertRaisesRegex(RecorderError, "timeout_seconds must be positive"):
            stop_process(1234, timeout_seconds=0)

    def test_stop_process_rejects_missing_kill_command(self) -> None:
        with mock.patch("speed_of_cinnamon.recorder.subprocess.run", side_effect=OSError("missing")):
            with self.assertRaisesRegex(RecorderError, "failed to run kill command"):
                stop_process(1234, timeout_seconds=0.1)

    def test_stop_process_rejects_kill_timeout(self) -> None:
        with mock.patch("speed_of_cinnamon.recorder.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="kill", timeout=1)):
            with self.assertRaisesRegex(RecorderError, "kill command timed out"):
                stop_process(1234, timeout_seconds=0.1)


if __name__ == "__main__":
    unittest.main()
