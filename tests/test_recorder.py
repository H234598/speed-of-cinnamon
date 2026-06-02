from __future__ import annotations

import subprocess
import os
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from speed_of_cinnamon.recorder import (
    RecorderCommand,
    RecorderError,
    _ensure_file_head,
    _file_size,
    choose_recorder,
    _run_kill,
    _run_pactl_command,
    normalize_input_device,
    list_input_sources,
    parse_pactl_sources,
    read_recording_level,
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
    def _write_wav(self, path: Path, samples: list[int]) -> None:
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(b"".join(sample.to_bytes(2, "little", signed=True) for sample in samples))

    def test_read_recording_level_reports_recent_peak(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            self._write_wav(audio, [0, 0, 16384, -32768])

            level = read_recording_level(audio)

        self.assertTrue(level.ok)
        self.assertEqual(level.percent, 100)
        self.assertGreater(level.rms, 0)
        self.assertEqual(level.samples, 4)

    def test_read_recording_level_waits_for_header_only_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"RIFF" + b"\x00" * 40)

            level = read_recording_level(audio)

        self.assertFalse(level.ok)
        self.assertEqual(level.percent, 0)
        self.assertEqual(level.detail, "waiting for audio")

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

    def test_choose_recorder_rejects_invalid_preference_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RecorderError, "preference must be text"):
                choose_recorder(123, Path(tmp) / "sample.wav", 10)  # type: ignore[arg-type]

    def test_choose_recorder_rejects_invalid_input_device_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio_path = Path(tmp) / "sample.wav"
            with mock.patch("speed_of_cinnamon.recorder.shutil.which", which_only("pw-record")):
                with self.assertRaisesRegex(RecorderError, "input device must be text"):
                    choose_recorder("pw-record", audio_path, 3, input_device=123)  # type: ignore[arg-type]

    def test_normalize_input_device_rejects_overly_long_name(self) -> None:
        with self.assertRaisesRegex(RecorderError, "input device name is too long"):
            normalize_input_device("x" * 300)

    def test_normalize_input_device_rejects_oversized_name_bytes(self) -> None:
        with mock.patch("speed_of_cinnamon.recorder.MAX_RECORDING_INPUT_DEVICE_CHARS", 4):
            with self.assertRaisesRegex(RecorderError, "input device name is too long"):
                normalize_input_device("😀" * 2)

    def test_normalize_input_device_rejects_null_byte(self) -> None:
        with self.assertRaisesRegex(RecorderError, "invalid null byte"):
            normalize_input_device("alsa\x00input")

    def test_normalize_input_device_rejects_escaped_null(self) -> None:
        with self.assertRaisesRegex(RecorderError, "invalid null byte"):
            normalize_input_device("alsa\\x00input")

    def test_validate_recording_path_rejects_wrong_extension(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}):
                with self.assertRaises(RecorderError):
                    validate_recording_path(Path(tmp) / "sample.txt", suffix=".wav")

    def test_validate_recording_path_rejects_null_byte(self) -> None:
        with self.assertRaisesRegex(RecorderError, "invalid null byte"):
            validate_recording_path(Path("sample\x00.wav"), suffix=".wav")

    def test_validate_recording_path_rejects_escaped_null(self) -> None:
        with self.assertRaisesRegex(RecorderError, "invalid null byte"):
            validate_recording_path(Path("sample\\x00.wav"), suffix=".wav")

    def test_validate_recording_path_rejects_oversized_path_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}):
                with self.assertRaisesRegex(RecorderError, "recording artifact path is too long"):
                    validate_recording_path(Path(tmp) / ("é" * 120 + ".wav"), suffix=".wav")

    def test_validate_recording_path_rejects_oversized_stem_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ("é" * 120 + ".wav")
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.recorder.MAX_RECORDING_PATH_CHARS", 10_000),
                mock.patch("speed_of_cinnamon.recorder.MAX_RECORDING_STEM_CHARS", 120),
            ):
                with self.assertRaisesRegex(RecorderError, "recording artifact stem is too long"):
                    validate_recording_path(path, suffix=".wav")

    def test_validate_recording_path_rejects_non_path_type(self) -> None:
        with self.assertRaisesRegex(RecorderError, "path must be a path"):
            validate_recording_path("sample.wav", suffix=".wav")  # type: ignore[arg-type]

    def test_validate_recording_path_rejects_non_text_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RecorderError, "suffix must be text"):
                validate_recording_path(Path(tmp) / "sample", suffix=123)  # type: ignore[arg-type]

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

    def test_start_recorder_rejects_invalid_log_path_type(self) -> None:
        command = RecorderCommand(name="noop", argv=["true"])
        with self.assertRaisesRegex(RecorderError, "invalid recorder log path"):
            start_recorder(command, "/tmp/session.log")  # type: ignore[arg-type]

    def test_start_recorder_resolves_recorder_command(self) -> None:
        command = RecorderCommand(name="noop", argv=["true"])
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.recorder.shutil.which", return_value="/usr/bin/true"),
                mock.patch("speed_of_cinnamon.recorder.subprocess.Popen") as mocked_popen,
            ):
                mocked_process = mock.Mock()
                mocked_popen.return_value = mocked_process
                result = start_recorder(command, Path(tmp) / "session.log")
        self.assertIs(result, mocked_process)
        self.assertEqual(mocked_popen.call_args.args[0][0], "/usr/bin/true")

    def test_start_recorder_sets_private_log_permissions(self) -> None:
        command = RecorderCommand(name="noop", argv=["true"])
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "session.log"
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.recorder.shutil.which", return_value="/usr/bin/true"),
                mock.patch("speed_of_cinnamon.recorder.subprocess.Popen") as mocked_popen,
            ):
                mocked_popen.return_value = mock.Mock()
                start_recorder(command, log_path)
            mode = log_path.stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)

    def test_start_recorder_rejects_non_text_argument(self) -> None:
        command = RecorderCommand(name="noop", argv=["true", 1])  # type: ignore[list-item]
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}):
                with self.assertRaisesRegex(RecorderError, "arguments must be text"):
                    start_recorder(command, Path(tmp) / "session.log")

    def test_start_recorder_rejects_missing_command(self) -> None:
        command = RecorderCommand(name="noop", argv=["true"])
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}):
                with mock.patch("speed_of_cinnamon.recorder.shutil.which", return_value=None):
                    with self.assertRaisesRegex(RecorderError, "true is not available"):
                        start_recorder(command, Path(tmp) / "session.log")

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

    def test_start_recorder_rejects_escaped_null_bytes(self) -> None:
        command = RecorderCommand(name="noop", argv=["true", "ok\\x00"])
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

    def test_run_pactl_command_rejects_escaped_null_bytes(self) -> None:
        with self.assertRaisesRegex(RecorderError, "pactl command contains invalid null byte"):
            _run_pactl_command(["pactl", "--name\\x00"], required=True)

    def test_run_pactl_command_rejects_bad_command_shape(self) -> None:
        with self.assertRaisesRegex(RecorderError, "invalid pactl command"):
            _run_pactl_command(("pactl", 10), required=True)  # type: ignore[arg-type]

    def test_run_pactl_command_accepts_tuple_command(self) -> None:
        calls: list[list[str]] = []

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            command = args[0] if args else kwargs["args"]
            assert isinstance(command, (list, tuple))
            command_list = list(command)
            calls.append(command_list)
            stdout = kwargs["stdout"]
            stderr = kwargs["stderr"]
            stdout.write(b"default\n")
            stderr.write(b"")
            return subprocess.CompletedProcess(command_list, 0, stdout=b"", stderr=b"")

        with (
            mock.patch("speed_of_cinnamon.recorder.shutil.which", return_value="/usr/bin/pactl"),
            mock.patch("speed_of_cinnamon.recorder.subprocess.run", side_effect=fake_run),
        ):
            result = _run_pactl_command(("pactl", "get-default-source"), required=False)

        self.assertEqual(result, "default")
        self.assertEqual(calls[0][0], "/usr/bin/pactl")

    def test_run_pactl_command_resolves_command_from_which(self) -> None:
        calls: list[list[str]] = []

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            command = kwargs["args"]
            assert isinstance(command, list)
            calls.append(command)
            stdout = kwargs["stdout"]
            stdout.write(b"default\n")
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with (
            mock.patch("speed_of_cinnamon.recorder.shutil.which", return_value="/usr/bin/pactl"),
            mock.patch("speed_of_cinnamon.recorder.subprocess.run", side_effect=fake_run),
        ):
            _run_pactl_command(["pactl"], required=False)

        self.assertEqual(calls, [["/usr/bin/pactl"]])

    def test_run_kill_rejects_bad_command_shape(self) -> None:
        with self.assertRaisesRegex(RecorderError, "invalid kill command"):
            _run_kill(("kill", "-9", 10), check_exit=True)  # type: ignore[arg-type]

    def test_run_kill_accepts_tuple_command(self) -> None:
        calls: list[list[str]] = []

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            command = args[0] if args else kwargs.get("args")
            assert isinstance(command, (list, tuple))
            calls.append(list(command))
            return subprocess.CompletedProcess(list(command), 0, stdout=b"", stderr=b"")

        with (
            mock.patch("speed_of_cinnamon.recorder.shutil.which", return_value="/usr/bin/kill"),
            mock.patch("speed_of_cinnamon.recorder.subprocess.run", side_effect=fake_run),
        ):
            _run_kill(("kill", "-INT", "1234"), check_exit=False)

        self.assertEqual(calls[0], ["/usr/bin/kill", "-INT", "1234"])

    def test_run_kill_rejects_non_bool_check(self) -> None:
        with self.assertRaisesRegex(RecorderError, "invalid kill command"):
            _run_kill(["kill", "-9", "123"], check_exit="false")  # type: ignore[arg-type]

    def test_run_kill_resolves_command_from_which(self) -> None:
        calls: list[list[str]] = []

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            command = args[0] if args else kwargs.get("args")
            assert isinstance(command, list)
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with (
            mock.patch("speed_of_cinnamon.recorder.shutil.which", return_value="/usr/bin/kill"),
            mock.patch("speed_of_cinnamon.recorder.subprocess.run", side_effect=fake_run),
        ):
            _run_kill(["kill", "-INT", "1234"], check_exit=True)

        self.assertEqual(calls[0][0], "/usr/bin/kill")

    def test_run_kill_rejects_missing_command(self) -> None:
        with mock.patch("speed_of_cinnamon.recorder.shutil.which", return_value=None):
            with self.assertRaisesRegex(RecorderError, "kill is not available"):
                _run_kill(["kill", "-INT", "1234"], check_exit=True)

    def test_parse_pactl_sources_rejects_non_text(self) -> None:
        with self.assertRaisesRegex(RecorderError, "invalid pactl source output"):
            parse_pactl_sources(None)  # type: ignore[arg-type]

    def test_parse_pactl_sources_rejects_bool(self) -> None:
        with self.assertRaisesRegex(RecorderError, "invalid pactl source output"):
            parse_pactl_sources(True)  # type: ignore[arg-type]

    def test_run_kill_rejects_null_bytes(self) -> None:
        with self.assertRaisesRegex(RecorderError, "kill command contains invalid null byte"):
            _run_kill(["kill", "-9", "\x00bad"], check_exit=False)

    def test_run_kill_rejects_escaped_null_bytes(self) -> None:
        with self.assertRaisesRegex(RecorderError, "kill command contains invalid null byte"):
            _run_kill(["kill", "-9", "\\x00bad"], check_exit=False)

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
            executable = command[0] if isinstance(command, list) and command else ""
            if str(executable).endswith("pactl") and command[1:] == ["get-default-source"]:
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

    def test_ensure_file_head_rejects_invalid_utf8(self) -> None:
        with tempfile.TemporaryFile() as handle:
            handle.write(b"ok\xff")
            with self.assertRaisesRegex(RecorderError, "not valid UTF-8"):
                _ensure_file_head(handle, 10)

    def test_ensure_file_head_rejects_non_positive_limits(self) -> None:
        with tempfile.TemporaryFile() as handle:
            handle.write(b"ok")
            with self.assertRaisesRegex(RecorderError, "max chars must be a positive integer"):
                _ensure_file_head(handle, 0)
            with self.assertRaisesRegex(RecorderError, "max chars must be a positive integer"):
                _ensure_file_head(handle, -1)
            with self.assertRaisesRegex(RecorderError, "max chars must be a positive integer"):
                _ensure_file_head(handle, True)  # type: ignore[arg-type]
            with self.assertRaisesRegex(RecorderError, "max chars must be a positive integer"):
                _ensure_file_head(handle, "10")  # type: ignore[arg-type]

    def test_ensure_file_head_rejects_escaped_null(self) -> None:
        with tempfile.TemporaryFile() as handle:
            handle.write("ok\\x00end".encode("utf-8"))
            with self.assertRaisesRegex(RecorderError, "contains invalid null byte"):
                _ensure_file_head(handle, 10)

    def test_ensure_file_head_rejects_invalid_file(self) -> None:
        with self.assertRaisesRegex(RecorderError, "binary file handle"):
            _ensure_file_head(object(), 10)  # type: ignore[arg-type]

    def test_file_size_rejects_invalid_file(self) -> None:
        with self.assertRaisesRegex(RecorderError, "binary file handle"):
            _file_size(object())  # type: ignore[arg-type]

    def test_choose_recorder_rejects_non_integer_max_seconds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("speed_of_cinnamon.recorder.shutil.which", which_only("pw-record")):
                with self.assertRaisesRegex(RecorderError, "must be an integer"):
                    choose_recorder("pw-record", Path(tmp) / "sample.wav", "3")  # type: ignore[arg-type]

    def test_stop_process_rejects_invalid_pid(self) -> None:
        with self.assertRaisesRegex(RecorderError, "invalid process id"):
            stop_process(0)

    def test_stop_process_rejects_non_positive_timeout(self) -> None:
        with self.assertRaisesRegex(RecorderError, "timeout_seconds must be positive"):
            stop_process(1234, timeout_seconds=0)

    def test_stop_process_rejects_non_numeric_timeout(self) -> None:
        with self.assertRaisesRegex(RecorderError, "timeout_seconds must be numeric"):
            stop_process(1234, timeout_seconds="5")  # type: ignore[arg-type]

    def test_stop_process_rejects_infinite_timeout(self) -> None:
        with self.assertRaisesRegex(RecorderError, "timeout_seconds must be finite"):
            stop_process(1234, timeout_seconds=float("inf"))

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
