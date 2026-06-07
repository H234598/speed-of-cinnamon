# mypy: ignore-errors
from __future__ import annotations

import subprocess
import os
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from speed_of_cinnamon import recorder as recorder_module
from speed_of_cinnamon.recorder import (
    RecorderCommand,
    RecorderError,
    SilenceDetectionResult,
    SILENCE_DETECT_DURATION_SECONDS,
    SILENCE_DETECT_NOISE,
    _ensure_file_head,
    _file_size,
    choose_recorder,
    detect_silent_recording,
    _run_kill,
    _run_pactl_command,
    normalize_input_device,
    list_input_sources,
    parse_pactl_sources,
    read_recording_level,
    trim_recording_leading_silence,
    trim_recording_silence,
    reencode_recording_to_flac,
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
    return mock.Mock(side_effect=lambda name, path=None: f"/usr/bin/{command}" if name == command else None)


def which_any(*commands: str) -> mock.Mock:
    allowed = set(commands)
    return mock.Mock(side_effect=lambda name, path=None: f"/usr/bin/{name}" if name in allowed else None)


class RecorderTest(unittest.TestCase):
    def _write_wav(self, path: Path, samples: list[int]) -> None:
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(b"".join(sample.to_bytes(2, "little", signed=True) for sample in samples))

    def _ffmpeg_success_with_output(self, command: list[str]) -> subprocess.CompletedProcess[bytes]:
        Path(command[-1]).write_bytes(b"audio")
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

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

    def test_detect_silent_recording_uses_ffmpeg_silencedetect(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "silent.wav"
            audio.write_bytes(b"RIFF" + b"\x00" * 44)
            stderr = (
                "Duration: 00:00:02.00, bitrate: 256 kb/s\n"
                "[silencedetect @ 0x1] silence_start: 0\n"
            )
            completed = subprocess.CompletedProcess(["ffmpeg"], 0, stdout="", stderr=stderr)
            with mock.patch("speed_of_cinnamon.recorder._command_path", return_value="/usr/bin/ffmpeg"):
                with mock.patch("speed_of_cinnamon.recorder.subprocess.run", return_value=completed) as mocked_run:
                    result = detect_silent_recording(audio)

            self.assertEqual(result, SilenceDetectionResult(True, True, 2.0, 2.0, 0.0, 2.0, "silent recording"))
            argv = mocked_run.call_args.args[0]
        self.assertIn("-nostdin", argv)
        self.assertIn(f"silencedetect=noise={SILENCE_DETECT_NOISE}:d={SILENCE_DETECT_DURATION_SECONDS}", argv)
        input_path = argv[argv.index("-i") + 1]
        self.assertTrue(str(input_path).startswith("/proc/self/fd/"))
        self.assertEqual(mocked_run.call_args.kwargs["pass_fds"], (int(str(input_path).rsplit("/", 1)[-1]),))
        self.assertNotIsInstance(argv, str)

    def test_detect_silent_recording_reports_leading_silence_seconds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "speech.wav"
            audio.write_bytes(b"RIFF" + b"\x00" * 44)
            stderr = (
                "Duration: 00:00:03.50, bitrate: 256 kb/s\n"
                "[silencedetect @ 0x1] silence_start: 0\n"
                "[silencedetect @ 0x1] silence_end: 1.25 | silence_duration: 1.25\n"
                "[silencedetect @ 0x1] silence_start: 2.50\n"
                "[silencedetect @ 0x1] silence_end: 2.75 | silence_duration: 0.25\n"
            )
            completed = subprocess.CompletedProcess(["ffmpeg"], 0, stdout="", stderr=stderr)
            with mock.patch("speed_of_cinnamon.recorder._command_path", return_value="/usr/bin/ffmpeg"):
                with mock.patch("speed_of_cinnamon.recorder.subprocess.run", return_value=completed):
                    result = detect_silent_recording(audio)

        self.assertFalse(result.silent)
        self.assertEqual(result.leading_silence_seconds, 1.25)
        self.assertEqual(result.silence_seconds, 1.5)
        self.assertEqual(result.speech_seconds, 2.0)

    def test_trim_recording_leading_silence_removes_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            self._write_wav(audio, [0] * 1600 + [12000] * 1600)

            trimmed = trim_recording_leading_silence(audio, 0.1)
            try:
                with wave.open(str(trimmed), "rb") as handle:
                    first_frame = int.from_bytes(handle.readframes(1), "little", signed=True)
                    frame_count = handle.getnframes()
            finally:
                trimmed.unlink(missing_ok=True)

        self.assertLess(frame_count, 3200)
        self.assertEqual(first_frame, 12000)

    def test_trim_recording_leading_silence_keeps_speech_on_fractional_start_frame(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            self._write_wav(audio, [0, 12000])

            trimmed = trim_recording_leading_silence(audio, 1.6 / 16000)
            try:
                with wave.open(str(trimmed), "rb") as handle:
                    first_frame = int.from_bytes(handle.readframes(1), "little", signed=True)
                    frame_count = handle.getnframes()
            finally:
                trimmed.unlink(missing_ok=True)

        self.assertEqual(frame_count, 1)
        self.assertEqual(first_frame, 12000)

    def test_trim_recording_leading_silence_rounds_near_integer_start_frame(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            self._write_wav(audio, [0, 0, 12000])

            trimmed = trim_recording_leading_silence(audio, (2 - 1e-10) / 16000)
            try:
                with wave.open(str(trimmed), "rb") as handle:
                    first_frame = int.from_bytes(handle.readframes(1), "little", signed=True)
                    frame_count = handle.getnframes()
            finally:
                trimmed.unlink(missing_ok=True)

        self.assertEqual(frame_count, 1)
        self.assertEqual(first_frame, 12000)

    def test_trim_recording_leading_silence_rejects_when_start_reaches_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            self._write_wav(audio, [0, 0])

            with self.assertRaisesRegex(RecorderError, "recording contains no speech"):
                trim_recording_leading_silence(audio, 2 / 16000)

    def test_trim_recording_leading_silence_rejects_symlink_recording_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            real_root = Path(tmp) / "real"
            link_root = Path(tmp) / "link"
            real_root.mkdir()
            link_root.symlink_to(real_root, target_is_directory=True)
            audio = link_root / "sample.wav"
            self._write_wav(audio, [0, 12000, 0, 12000])

            with self.assertRaisesRegex(RecorderError, "recording artifact path must not pass through a symlink"):
                trim_recording_leading_silence(audio, 0.1)

    def test_trim_recording_silence_converts_to_flac(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")

            with mock.patch("speed_of_cinnamon.recorder._command_path", return_value="/usr/bin/ffmpeg"):
                with mock.patch(
                    "speed_of_cinnamon.recorder.subprocess.run",
                    side_effect=lambda *args, **kwargs: self._ffmpeg_success_with_output(args[0]),
                ) as mocked_run:
                    trimmed = trim_recording_silence(audio)
                    trimmed_exists = trimmed.exists()

        self.assertEqual(trimmed.suffix, ".flac")
        self.assertTrue(trimmed_exists)
        mocked_run.assert_called_once()
        argv = mocked_run.call_args.args[0]
        self.assertIn("-c:a", argv)
        self.assertIn("flac", argv)
        expected = (
            f"silenceremove=start_periods=1:start_duration={SILENCE_DETECT_DURATION_SECONDS}:"
            f"start_threshold={SILENCE_DETECT_NOISE}:stop_periods=1:"
            f"stop_duration={SILENCE_DETECT_DURATION_SECONDS}:stop_threshold={SILENCE_DETECT_NOISE}"
        )
        self.assertIn(
            expected,
            "".join(argv),
        )
        self.assertIn("-f", argv)
        self.assertIn("flac", argv)
        input_path = argv[argv.index("-i") + 1]
        self.assertTrue(str(input_path).startswith("/proc/self/fd/"))
        self.assertTrue(str(argv[-1]).startswith("/proc/self/fd/"))
        self.assertEqual(
            mocked_run.call_args.kwargs["pass_fds"],
            (
                int(str(input_path).rsplit("/", 1)[-1]),
                int(str(argv[-1]).rsplit("/", 1)[-1]),
            ),
        )

    def test_trim_recording_silence_rejects_hardlinked_audio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            hardlink = Path(tmp) / "sample-hardlink.wav"
            try:
                os.link(audio, hardlink)
            except OSError as exc:
                self.skipTest(f"hardlinks unavailable: {exc}")

            with self.assertRaisesRegex(RecorderError, "recording audio file must not be hardlinked"):
                trim_recording_silence(hardlink)

    def test_reencode_recording_to_flac_uses_ffmpeg_flac_encoder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            completed = subprocess.CompletedProcess(["ffmpeg"], 0, stdout=b"", stderr=b"")
            with mock.patch("speed_of_cinnamon.recorder._command_path", return_value="/usr/bin/ffmpeg"):
                with mock.patch(
                    "speed_of_cinnamon.recorder.subprocess.run",
                    side_effect=lambda *args, **kwargs: self._ffmpeg_success_with_output(args[0]),
                ) as mocked_run:
                    output = reencode_recording_to_flac(audio)
                    output_exists = output.exists()

        self.assertEqual(output.suffix, ".flac")
        self.assertTrue(output_exists)
        argv = mocked_run.call_args.args[0]
        self.assertIn("-c:a", argv)
        self.assertIn("flac", argv)
        self.assertIn("-f", argv)
        input_path = argv[argv.index("-i") + 1]
        self.assertTrue(str(input_path).startswith("/proc/self/fd/"))
        self.assertTrue(str(argv[-1]).startswith("/proc/self/fd/"))
        self.assertEqual(
            mocked_run.call_args.kwargs["pass_fds"],
            (
                int(str(input_path).rsplit("/", 1)[-1]),
                int(str(argv[-1]).rsplit("/", 1)[-1]),
            ),
        )

    def test_reencode_recording_to_flac_rejects_hardlinked_audio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            hardlink = Path(tmp) / "sample-hardlink.wav"
            try:
                os.link(audio, hardlink)
            except OSError as exc:
                self.skipTest(f"hardlinks unavailable: {exc}")

            with self.assertRaisesRegex(RecorderError, "recording audio file must not be hardlinked"):
                reencode_recording_to_flac(hardlink)

    def test_recording_temp_artifacts_do_not_use_closed_mkstemp_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            self._write_wav(audio, [0, 12000, 12000])
            with mock.patch("speed_of_cinnamon.recorder.tempfile.mkstemp", side_effect=AssertionError("mkstemp used")):
                trimmed = trim_recording_leading_silence(audio, 1 / 16000)
            try:
                self.assertTrue(trimmed.exists())
            finally:
                trimmed.unlink(missing_ok=True)

    def test_trim_recording_silence_rejects_replaced_temp_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            with mock.patch("speed_of_cinnamon.recorder._command_path", return_value="/usr/bin/ffmpeg"):
                with mock.patch(
                    "speed_of_cinnamon.recorder.subprocess.run",
                    side_effect=lambda *args, **kwargs: self._ffmpeg_success_with_output(args[0]),
                ):
                    with mock.patch.object(recorder_module, "_recording_temp_path_matches_fd", return_value=False):
                        with self.assertRaisesRegex(RecorderError, "temporary file was replaced"):
                            trim_recording_silence(audio)

    def test_trim_recording_silence_reports_ffmpeg_error_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            failed = subprocess.CompletedProcess(["ffmpeg"], 1, stdout=b"", stderr=b"bad audio")
            with mock.patch("speed_of_cinnamon.recorder._command_path", return_value="/usr/bin/ffmpeg"):
                with mock.patch("speed_of_cinnamon.recorder.subprocess.run", return_value=failed):
                    with self.assertRaisesRegex(RecorderError, "bad audio"):
                        trim_recording_silence(audio)

    def test_trim_recording_silence_redacts_ffmpeg_path_error_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "secret-sample.wav"
            audio.write_bytes(b"audio")
            failed = subprocess.CompletedProcess(["ffmpeg"], 1, stdout=b"", stderr=f"failed {audio} token secret".encode())
            with mock.patch("speed_of_cinnamon.recorder._command_path", return_value="/usr/bin/ffmpeg"):
                with mock.patch("speed_of_cinnamon.recorder.subprocess.run", return_value=failed):
                    with self.assertRaisesRegex(RecorderError, "\\[redacted ffmpeg error\\]") as raised:
                        trim_recording_silence(audio)
            self.assertNotIn(str(audio), str(raised.exception))
            self.assertNotIn("secret", str(raised.exception))

    def test_reencode_recording_to_flac_reports_ffmpeg_error_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            failed = subprocess.CompletedProcess(["ffmpeg"], 1, stdout=b"", stderr=b"encoder failed")
            with mock.patch("speed_of_cinnamon.recorder._command_path", return_value="/usr/bin/ffmpeg"):
                with mock.patch("speed_of_cinnamon.recorder.subprocess.run", return_value=failed):
                    with self.assertRaisesRegex(RecorderError, "encoder failed"):
                        reencode_recording_to_flac(audio)

    def test_reencode_recording_to_flac_redacts_ffmpeg_path_error_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "secret-sample.wav"
            audio.write_bytes(b"audio")
            failed = subprocess.CompletedProcess(["ffmpeg"], 1, stdout=b"", stderr=f"failed {audio} token secret".encode())
            with mock.patch("speed_of_cinnamon.recorder._command_path", return_value="/usr/bin/ffmpeg"):
                with mock.patch("speed_of_cinnamon.recorder.subprocess.run", return_value=failed):
                    with self.assertRaisesRegex(RecorderError, "\\[redacted ffmpeg error\\]") as raised:
                        reencode_recording_to_flac(audio)
            self.assertNotIn(str(audio), str(raised.exception))
            self.assertNotIn("secret", str(raised.exception))

    def test_detect_silent_recording_fails_open_when_ffmpeg_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"RIFF" + b"\x00" * 44)
            with mock.patch("speed_of_cinnamon.recorder._command_path", side_effect=RecorderError("ffmpeg missing")):
                result = detect_silent_recording(audio)

        self.assertFalse(result.analyzed)
        self.assertFalse(result.silent)
        self.assertIn("ffmpeg missing", result.detail)

    def test_detect_silent_recording_redacts_subprocess_exception_detail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "secret-token-recording.wav"
            audio.write_bytes(b"RIFF" + b"\x00" * 44)
            error = OSError(f"failed {audio} token secret")
            with (
                mock.patch("speed_of_cinnamon.recorder._command_path", return_value="/usr/bin/ffmpeg"),
                mock.patch("speed_of_cinnamon.recorder.subprocess.run", side_effect=error),
            ):
                result = detect_silent_recording(audio)

        self.assertFalse(result.analyzed)
        self.assertIn("[redacted ffmpeg error]", result.detail)
        self.assertNotIn(str(audio), result.detail)
        self.assertNotIn("secret", result.detail)

    def test_detect_silent_recording_rejects_symlink_audio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target.wav"
            link = Path(tmp) / "link.wav"
            target.write_bytes(b"RIFF" + b"\x00" * 44)
            link.symlink_to(target)

            with self.assertRaisesRegex(RecorderError, "must not pass through a symlink"):
                detect_silent_recording(link)

    def test_detect_silent_recording_rejects_hardlinked_audio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"RIFF" + b"\x00" * 44)
            hardlink = Path(tmp) / "sample-hardlink.wav"
            try:
                os.link(audio, hardlink)
            except OSError as exc:
                self.skipTest(f"hardlinks unavailable: {exc}")

            with self.assertRaisesRegex(RecorderError, "recording audio file must not be hardlinked"):
                detect_silent_recording(hardlink)

    @mock.patch("speed_of_cinnamon.recorder.os.open", wraps=os.open)
    def test_read_recording_level_uses_secure_open_flags(self, mocked_open: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            self._write_wav(audio, [0, 1, 2, 3])
            level = read_recording_level(audio)

        self.assertTrue(level.ok)
        self.assertTrue(
            any(
                Path(args[0]) == audio and isinstance(args[1], int) and args[1] & os.O_NOFOLLOW
                for args, _ in mocked_open.call_args_list
            )
        )

    def test_read_recording_level_rejects_hardlinked_audio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            self._write_wav(audio, [0, 1, 2, 3])
            hardlink = Path(tmp) / "sample-hardlink.wav"
            try:
                os.link(audio, hardlink)
            except OSError as exc:
                self.skipTest(f"hardlinks unavailable: {exc}")

            with self.assertRaisesRegex(RecorderError, "not readable"):
                read_recording_level(hardlink)

    def test_read_recording_level_rejects_fifo_without_blocking(self) -> None:
        if not hasattr(os, "mkfifo"):
            self.skipTest("mkfifo unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            fifo = Path(tmp) / "sample.wav"
            os.mkfifo(fifo)

            with self.assertRaisesRegex(RecorderError, "not readable"):
                read_recording_level(fifo)

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
            with mock.patch("speed_of_cinnamon.recorder.shutil.which", which_any("parecord", "timeout")):
                command = choose_recorder("parecord", audio_path, 3, "alsa_input.usb-mic")
        self.assertEqual(command.name, "parecord")
        self.assertEqual(command.argv[:4], ["timeout", "--kill-after=1", "3", "parecord"])
        self.assertIn("--device=alsa_input.usb-mic", command.argv)
        self.assertEqual(command.argv[-1], str(audio_path))

    def test_choose_parecord_requires_timeout_for_finite_recordings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio_path = Path(tmp) / "sample.wav"
            with mock.patch("speed_of_cinnamon.recorder.shutil.which", which_only("parecord")):
                with self.assertRaisesRegex(RecorderError, "timeout is required"):
                    choose_recorder("parecord", audio_path, 3)

    def test_choose_parecord_allows_unlimited_recordings_without_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio_path = Path(tmp) / "sample.wav"
            with mock.patch("speed_of_cinnamon.recorder.shutil.which", which_only("parecord")):
                command = choose_recorder("parecord", audio_path, 0)
        self.assertEqual(command.name, "parecord")
        self.assertEqual(command.argv[0], "parecord")

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

    def test_choose_recorder_rejects_control_character_preference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RecorderError, "preference contains invalid control character"):
                choose_recorder("\x85pw-record", Path(tmp) / "sample.wav", 10)

    def test_choose_recorder_rejects_escaped_control_character_preference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RecorderError, "preference contains invalid control character"):
                choose_recorder("\\x85pw-record", Path(tmp) / "sample.wav", 10)

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

    def test_normalize_input_device_rejects_c1_control_character(self) -> None:
        with self.assertRaisesRegex(RecorderError, "invalid control character"):
            normalize_input_device("alsa\x85input")

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

    def test_validate_recording_path_rejects_control_character(self) -> None:
        with self.assertRaisesRegex(RecorderError, "invalid control character"):
            validate_recording_path(Path("sample\nspoof.wav"), suffix=".wav")

    def test_validate_recording_path_rejects_escaped_control_character(self) -> None:
        with self.assertRaisesRegex(RecorderError, "invalid control character"):
            validate_recording_path(Path("sample\\nspoof.wav"), suffix=".wav")

    def test_validate_recording_path_rejects_c1_control_character(self) -> None:
        with self.assertRaisesRegex(RecorderError, "invalid control character"):
            validate_recording_path(Path("sample\x85spoof.wav"), suffix=".wav")

    def test_validate_recording_path_rejects_escaped_c1_control_character(self) -> None:
        with self.assertRaisesRegex(RecorderError, "invalid control character"):
            validate_recording_path(Path("sample\\x85spoof.wav"), suffix=".wav")

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

    def test_validate_recording_path_accepts_wav_or_flac_suffix_tuple(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}):
                audio = Path(tmp) / "sample.flac"
                audio.write_bytes(b"audio")
                valid = validate_recording_path(audio, suffix=(".wav", ".flac"))
            self.assertEqual(valid.suffix, ".flac")

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

    def test_start_recorder_filters_dangerous_environment_variables(self) -> None:
        command = RecorderCommand(name="noop", argv=["true"])
        captured_env: dict[str, str] = {}

        def fake_popen(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
            env = kwargs.get("env")
            if isinstance(env, dict):
                captured_env.update(env)
            return mock.Mock()

        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}),
                mock.patch.dict(
                    "speed_of_cinnamon.recorder.os.environ",
                    {
                        "LD_PRELOAD": "malicious-lib.so",
                        "PYTHONPATH": "/tmp/evil",
                        "HOME": "/tmp/home",
                        "LANG": "en_US.UTF-8",
                        "XDG_RUNTIME_DIR": "/run/user/1000",
                        "PULSE_SERVER": "unix:/run/user/1000/pulse/native",
                        "PIPEWIRE_REMOTE": "pipewire-0",
                    },
                    clear=True,
                ),
                mock.patch("speed_of_cinnamon.recorder.shutil.which", return_value="/usr/bin/true"),
                mock.patch("speed_of_cinnamon.recorder.subprocess.Popen", side_effect=fake_popen),
            ):
                start_recorder(command, Path(tmp) / "session.log")

        self.assertNotIn("LD_PRELOAD", captured_env)
        self.assertNotIn("PYTHONPATH", captured_env)
        self.assertEqual(captured_env["XDG_RUNTIME_DIR"], "/run/user/1000")
        self.assertEqual(captured_env["PULSE_SERVER"], "unix:/run/user/1000/pulse/native")
        self.assertEqual(captured_env["PIPEWIRE_REMOTE"], "pipewire-0")
        self.assertEqual(captured_env["PATH"], "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")

    def test_filtered_environment_skips_non_text_environment_values(self) -> None:
        from speed_of_cinnamon.recorder import _filtered_environment as recorder_filtered_environment

        with mock.patch("speed_of_cinnamon.recorder.os.environ.__getitem__", return_value=123):
            env = recorder_filtered_environment()

        self.assertNotIn("HOME", env)
        self.assertNotIn("LANG", env)
        self.assertIn("PATH", env)

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

    def test_start_recorder_opens_log_file_without_following_symlinks(self) -> None:
        command = RecorderCommand(name="noop", argv=["true"])
        captured: dict[str, object] = {}
        fake_log_file = mock.Mock()
        fake_log_file.fileno.return_value = 11

        next_fd = 10

        def fake_os_open(path: Path | str, flags: int, mode: int = 0o600, **kwargs: object) -> int:
            nonlocal next_fd
            next_fd += 1
            if path == log_path.name:
                captured["path"] = path
                captured["flags"] = flags
                captured["mode"] = mode
                captured["dir_fd"] = kwargs.get("dir_fd")
            return next_fd

        real_os_stat = os.stat

        def fake_os_stat(path: Path | str, *args: object, **kwargs: object) -> os.stat_result:
            if path == log_path.name and kwargs.get("dir_fd") is not None and kwargs.get("follow_symlinks") is False:
                raise FileNotFoundError(path)
            return real_os_stat(path, *args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "session.log"
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.recorder.shutil.which", return_value="/usr/bin/true"),
                mock.patch("speed_of_cinnamon.recorder.os.open", side_effect=fake_os_open),
                mock.patch("speed_of_cinnamon.recorder.os.stat", side_effect=fake_os_stat),
                mock.patch("speed_of_cinnamon.recorder.os.close"),
                mock.patch("speed_of_cinnamon.recorder.os.fdopen", return_value=fake_log_file),
                mock.patch("speed_of_cinnamon.recorder.os.fchmod"),
                mock.patch("speed_of_cinnamon.recorder.assert_fd_is_regular_private_file"),
                mock.patch("speed_of_cinnamon.recorder.subprocess.Popen") as mocked_popen,
            ):
                mocked_popen.return_value = mock.Mock()
                start_recorder(command, log_path)

        self.assertEqual(captured["path"], log_path.name)
        self.assertEqual(captured["mode"], 0o600)
        self.assertIsInstance(captured["dir_fd"], int)
        self.assertTrue(captured["flags"] & os.O_APPEND)
        self.assertTrue(captured["flags"] & os.O_CREAT)
        self.assertTrue(captured["flags"] & os.O_EXCL)
        self.assertTrue(captured["flags"] & os.O_NOFOLLOW)

    def test_start_recorder_rejects_symlink_log_leaf_after_validation(self) -> None:
        command = RecorderCommand(name="noop", argv=["true"])
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            target = base / "foreign.log"
            target.write_bytes(b"foreign-data")
            log_path = base / "session.log"
            log_path.symlink_to(target)
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.recorder.validate_recording_path", return_value=log_path),
                mock.patch("speed_of_cinnamon.recorder.shutil.which", return_value="/usr/bin/true"),
            ):
                with self.assertRaisesRegex(RecorderError, "failed to open recorder log file"):
                    start_recorder(command, log_path)

            self.assertEqual(target.read_bytes(), b"foreign-data")

    def test_start_recorder_rejects_symlink_log_parent_after_validation(self) -> None:
        command = RecorderCommand(name="noop", argv=["true"])
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            real = base / "foreign"
            real.mkdir()
            link = base / "logs"
            link.symlink_to(real, target_is_directory=True)
            log_path = link / "session.log"
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.recorder.validate_recording_path", return_value=log_path),
                mock.patch("speed_of_cinnamon.recorder.shutil.which", return_value="/usr/bin/true"),
            ):
                with self.assertRaisesRegex(RecorderError, "failed to open recorder log file"):
                    start_recorder(command, log_path)

            self.assertFalse((real / "session.log").exists())

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

    def test_start_recorder_rejects_escaped_newline(self) -> None:
        command = RecorderCommand(name="noop", argv=["true", "ok\\r\\n"])
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}):
                with self.assertRaisesRegex(RecorderError, "recorder command contains invalid control character"):
                    start_recorder(command, Path(tmp) / "session.log")

    @mock.patch("speed_of_cinnamon.recorder.subprocess.Popen", side_effect=OSError("boom"))
    def test_start_recorder_cleans_up_log_file_when_start_fails(self, mocked_popen: mock.Mock) -> None:
        command = RecorderCommand(name="noop", argv=["true"])
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "session.log"
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}):
                with self.assertRaisesRegex(RecorderError, "failed to start noop"):
                    start_recorder(command, log_path)
            self.assertFalse(log_path.exists())
        mocked_popen.assert_called_once()

    @mock.patch("speed_of_cinnamon.recorder.subprocess.Popen", side_effect=OSError("boom"))
    def test_start_recorder_fsyncs_parent_when_start_failure_removes_log(self, mocked_popen: mock.Mock) -> None:
        from speed_of_cinnamon import recorder as recorder_module

        command = RecorderCommand(name="noop", argv=["true"])
        fsync_modes: list[int] = []
        real_fsync = os.fsync

        def record_fsync(fd: int) -> None:
            fsync_modes.append(os.fstat(fd).st_mode)
            real_fsync(fd)

        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "session.log"
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.recorder.os.fsync", side_effect=record_fsync),
            ):
                with self.assertRaisesRegex(RecorderError, "failed to start noop"):
                    start_recorder(command, log_path)
            self.assertFalse(log_path.exists())

        self.assertTrue(
            any(recorder_module.stat.S_ISDIR(mode) for mode in fsync_modes),
            "recorder log cleanup should fsync the parent directory after unlink",
        )
        mocked_popen.assert_called_once()

    def test_unlink_recording_path_if_same_fsyncs_parent_after_delete(self) -> None:
        from speed_of_cinnamon import recorder as recorder_module

        fsync_modes: list[int] = []
        real_fsync = os.fsync

        def record_fsync(fd: int) -> None:
            fsync_modes.append(os.fstat(fd).st_mode)
            real_fsync(fd)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "recording.flac"
            path.write_bytes(b"audio")
            expected_stat = path.stat()
            with mock.patch("speed_of_cinnamon.recorder.os.fsync", side_effect=record_fsync):
                recorder_module._unlink_recording_path_if_same(path, expected_stat)
            self.assertFalse(path.exists())

        self.assertTrue(
            any(recorder_module.stat.S_ISDIR(mode) for mode in fsync_modes),
            "recording temp cleanup should fsync the parent directory after unlink",
        )

    def test_start_recorder_rejects_hardlinked_existing_log_file(self) -> None:
        command = RecorderCommand(name="noop", argv=["true"])
        with tempfile.TemporaryDirectory() as tmp:
            original = Path(tmp) / "base.log"
            original.write_text("existing", encoding="utf-8")
            log_path = Path(tmp) / "session.log"
            try:
                os.link(original, log_path)
            except OSError as exc:
                self.skipTest(f"hardlinks unavailable: {exc}")

            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}):
                with self.assertRaisesRegex(RecorderError, "failed to open recorder log file"):
                    start_recorder(command, log_path)

            self.assertTrue(log_path.exists())
            self.assertEqual(log_path.read_text(encoding="utf-8"), "existing")

    @mock.patch("speed_of_cinnamon.recorder.subprocess.Popen", side_effect=OSError("boom"))
    def test_start_recorder_keeps_existing_log_file_when_start_fails(self, mocked_popen: mock.Mock) -> None:
        command = RecorderCommand(name="noop", argv=["true"])
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "session.log"
            log_path.write_text("previous content", encoding="utf-8")
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}):
                with self.assertRaisesRegex(RecorderError, "failed to start noop"):
                    start_recorder(command, log_path)
            self.assertEqual(log_path.read_text(encoding="utf-8"), "previous content")
        mocked_popen.assert_called_once()

    @mock.patch("speed_of_cinnamon.recorder.subprocess.Popen", side_effect=OSError("boom"))
    def test_start_recorder_keeps_existing_empty_log_file_when_start_fails(self, mocked_popen: mock.Mock) -> None:
        command = RecorderCommand(name="noop", argv=["true"])
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "session.log"
            log_path.write_text("", encoding="utf-8")
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}):
                with self.assertRaisesRegex(RecorderError, "failed to start noop"):
                    start_recorder(command, log_path)
            self.assertTrue(log_path.exists())
            self.assertEqual(log_path.read_text(encoding="utf-8"), "")
        mocked_popen.assert_called_once()

    @mock.patch("speed_of_cinnamon.recorder.subprocess.Popen", side_effect=OSError("boom"))
    def test_start_recorder_does_not_unlink_replaced_log_file_on_start_failure(self, mocked_popen: mock.Mock) -> None:
        command = RecorderCommand(name="noop", argv=["true"])
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "session.log"
            replacement = Path(tmp) / "replacement.log"
            replacement.write_bytes(b"replaced")

            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.recorder.shutil.which", return_value="/usr/bin/true"),
            ):
                original_fstat = os.fstat
                seen = 0

                def fake_fstat(fd: int) -> os.stat_result:
                    stat_result = original_fstat(fd)
                    nonlocal seen
                    seen += 1
                    if seen == 2:
                        os.replace(replacement, log_path)
                    return stat_result

                with mock.patch("speed_of_cinnamon.recorder.os.fstat", side_effect=fake_fstat):
                    with self.assertRaisesRegex(RecorderError, "failed to start noop"):
                        start_recorder(command, log_path)

            self.assertTrue(log_path.exists())
            self.assertEqual(log_path.read_bytes(), b"replaced")
        mocked_popen.assert_called_once()

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

    def test_run_pactl_command_rejects_control_chars(self) -> None:
        with self.assertRaisesRegex(RecorderError, "pactl command contains invalid control character"):
            _run_pactl_command(["pactl", "bad\\r"], required=True)

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

    def test_run_pactl_command_filters_dangerous_environment_variables(self) -> None:
        captured_env: dict[str, str] = {}

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            env = kwargs.get("env")
            if isinstance(env, dict):
                captured_env.update(env)
            stdout = kwargs["stdout"]
            stdout.write(b"default\n")
            return subprocess.CompletedProcess(["pactl"], 0, stdout=b"", stderr=b"")

        with (
            mock.patch.dict(
                "speed_of_cinnamon.recorder.os.environ",
                {
                    "LD_PRELOAD": "malicious-lib.so",
                    "PYTHONPATH": "/tmp/evil",
                    "HOME": "/tmp/home",
                    "LANG": "en_US.UTF-8",
                    "XDG_RUNTIME_DIR": "/run/user/1000",
                    "PULSE_SERVER": "unix:/run/user/1000/pulse/native",
                },
                clear=True,
            ),
            mock.patch("speed_of_cinnamon.recorder.shutil.which", return_value="/usr/bin/pactl"),
            mock.patch("speed_of_cinnamon.recorder.subprocess.run", side_effect=fake_run),
        ):
            _run_pactl_command(["pactl"], required=True)

        self.assertNotIn("LD_PRELOAD", captured_env)
        self.assertNotIn("PYTHONPATH", captured_env)
        self.assertEqual(captured_env["XDG_RUNTIME_DIR"], "/run/user/1000")
        self.assertEqual(captured_env["PULSE_SERVER"], "unix:/run/user/1000/pulse/native")
        self.assertEqual(captured_env["PATH"], "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")

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

    def test_run_kill_filters_dangerous_environment_variables(self) -> None:
        captured_env: dict[str, str] = {}

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            env = kwargs.get("env")
            if isinstance(env, dict):
                captured_env.update(env)
            return subprocess.CompletedProcess(["kill"], 0, stdout=b"", stderr=b"")

        with (
            mock.patch.dict(
                "speed_of_cinnamon.recorder.os.environ",
                {
                    "LD_PRELOAD": "malicious-lib.so",
                    "PYTHONPATH": "/tmp/evil",
                    "HOME": "/tmp/home",
                    "LANG": "en_US.UTF-8",
                    "XDG_RUNTIME_DIR": "/run/user/1000",
                    "PULSE_SERVER": "unix:/run/user/1000/pulse/native",
                },
                clear=True,
            ),
            mock.patch("speed_of_cinnamon.recorder.shutil.which", return_value="/usr/bin/kill"),
            mock.patch("speed_of_cinnamon.recorder.subprocess.run", side_effect=fake_run),
        ):
            _run_kill(["kill", "-INT", "1234"], check_exit=False)

        self.assertNotIn("LD_PRELOAD", captured_env)
        self.assertNotIn("PYTHONPATH", captured_env)
        self.assertEqual(captured_env["XDG_RUNTIME_DIR"], "/run/user/1000")
        self.assertEqual(captured_env["PULSE_SERVER"], "unix:/run/user/1000/pulse/native")
        self.assertEqual(captured_env["PATH"], "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")

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

    def test_run_kill_rejects_control_chars(self) -> None:
        with self.assertRaisesRegex(RecorderError, "kill command contains invalid control character"):
            _run_kill(["kill", "-9", "bad\\n"], check_exit=False)

    def test_run_kill_rejects_command_with_path_separator(self) -> None:
        with self.assertRaisesRegex(RecorderError, "path separators"):
            _run_kill(["/usr/bin/kill", "-9", "1234"], check_exit=False)

    def test_run_pactl_command_rejects_command_with_path_separator(self) -> None:
        with self.assertRaisesRegex(RecorderError, "path separators"):
            _run_pactl_command(["/usr/bin/pactl", "get-default-source"], required=False)

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
        with (
            mock.patch("speed_of_cinnamon.recorder.os.getpgid", return_value=1234),
            mock.patch("speed_of_cinnamon.recorder.subprocess.run", side_effect=OSError("missing")),
        ):
            with self.assertRaisesRegex(RecorderError, "failed to run kill command"):
                stop_process(1234, timeout_seconds=0.1)

    def test_stop_process_rejects_kill_timeout(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.recorder.os.getpgid", return_value=1234),
            mock.patch("speed_of_cinnamon.recorder.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="kill", timeout=1)),
        ):
            with self.assertRaisesRegex(RecorderError, "kill command timed out"):
                stop_process(1234, timeout_seconds=0.1)

    def test_stop_process_signals_recorder_process_group(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.recorder.os.getpgid", return_value=1234),
            mock.patch(
                "speed_of_cinnamon.recorder._run_kill",
                side_effect=[None, subprocess.CalledProcessError(1, ["kill"])],
            ) as mocked_kill,
        ):
            stop_process(1234, timeout_seconds=0.1)

        self.assertEqual(mocked_kill.call_args_list[0].args[0], ["kill", "-INT", "--", "-1234"])
        self.assertEqual(mocked_kill.call_args_list[1].args[0], ["kill", "-0", "--", "-1234"])

    def test_stop_process_signals_pid_when_process_is_not_group_leader(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.recorder.os.getpgid", return_value=999),
            mock.patch(
                "speed_of_cinnamon.recorder._run_kill",
                side_effect=[None, subprocess.CalledProcessError(1, ["kill"])],
            ) as mocked_kill,
        ):
            stop_process(1234, timeout_seconds=0.1)

        self.assertEqual(mocked_kill.call_args_list[0].args[0], ["kill", "-INT", "--", "1234"])
        self.assertEqual(mocked_kill.call_args_list[1].args[0], ["kill", "-0", "--", "1234"])

    def test_stop_process_returns_when_process_is_already_gone(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.recorder.os.getpgid", side_effect=ProcessLookupError),
            mock.patch("speed_of_cinnamon.recorder._run_kill") as mocked_kill,
        ):
            stop_process(1234, timeout_seconds=0.1)

        mocked_kill.assert_not_called()


if __name__ == "__main__":
    unittest.main()
