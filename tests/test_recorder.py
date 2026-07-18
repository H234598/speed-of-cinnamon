# mypy: ignore-errors
from __future__ import annotations

import subprocess
import os
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest import mock

from speed_of_cinnamon import recorder as recorder_module
from speed_of_cinnamon.recorder import (
    RecorderCommand,
    _assert_valid_input_device,
    RecorderError,
    SilenceDetectionResult,
    SILENCE_DETECT_DURATION_SECONDS,
    SILENCE_DETECT_NOISE,
    _ensure_file_head,
    _file_size,
    _completed_output_bytes,
    _decode_ffmpeg_output,
    _parse_silence_seconds,
    _wav_data_offset,
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


class _FakePopen:
    def __init__(self, result: subprocess.CompletedProcess[bytes]) -> None:
        self._result = result
        self.pid = 12345
        self.returncode = result.returncode
        self.stdout = result.stdout
        self.stderr = result.stderr

    def communicate(self, input: bytes | None = None, timeout: int | None = None) -> tuple[bytes | None, bytes | None]:
        return self._result.stdout, self._result.stderr

    def kill(self) -> None:
        self.returncode = -9

    def poll(self) -> int | None:
        return self.returncode


class _RunnerPopen(_FakePopen):
    def __init__(self, runner: object, args: tuple[object, ...], kwargs: dict[str, object]) -> None:
        super().__init__(subprocess.CompletedProcess([], 0, stdout=b"", stderr=b""))
        self._runner = runner
        self._args = args
        self._kwargs = kwargs
        self._completed = False

    def communicate(self, input: bytes | None = None, timeout: int | None = None) -> tuple[bytes | None, bytes | None]:
        if not self._completed:
            call_kwargs = dict(self._kwargs)
            call_kwargs["input"] = input
            result = self._runner(*self._args, **call_kwargs)  # type: ignore[operator]
            assert isinstance(result, subprocess.CompletedProcess)
            self._result = result
            self.returncode = result.returncode
            self.stdout = result.stdout
            self.stderr = result.stderr
            self._completed = True
        return self._result.stdout, self._result.stderr


class _TimeoutPopen(_FakePopen):
    def __init__(self, command: object) -> None:
        super().__init__(subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b""))
        self.returncode = None

    def communicate(self, input: bytes | None = None, timeout: int | None = None) -> tuple[bytes | None, bytes | None]:
        if timeout is not None:
            raise subprocess.TimeoutExpired(cmd=self._result.args, timeout=timeout)
        return b"", b""


def _popen_from_run(runner: object):
    def factory(*args: object, **kwargs: object) -> _FakePopen:
        return _RunnerPopen(runner, args, kwargs)

    return factory


class RecorderTest(unittest.TestCase):
    def test_fsync_retries_interrupted_calls(self) -> None:
        with mock.patch.object(recorder_module.os, "fsync", side_effect=[InterruptedError(), None]) as mocked_fsync:
            recorder_module._fsync_fd(123)

        self.assertEqual(mocked_fsync.call_count, 2)

    def test_close_fd_quietly_swallows_interrupt(self) -> None:
        with mock.patch.object(recorder_module.os, "close", side_effect=KeyboardInterrupt("close interrupted")):
            recorder_module._close_fd_quietly(42)

    def test_reap_does_not_signal_already_reaped_recorder_process(self) -> None:
        process = mock.Mock()
        process.pid = 1234
        process.poll.return_value = 0
        process.communicate.return_value = (b"", b"")
        with mock.patch("speed_of_cinnamon.recorder.os.killpg") as mocked_killpg:
            self.assertTrue(recorder_module._reap_timed_out_recorder_process(process))

        mocked_killpg.assert_not_called()
        process.communicate.assert_called_once_with(timeout=1)

    def test_reaped_process_group_cleanup_kills_live_descendants(self) -> None:
        process = subprocess.Popen(
            ["/bin/sh", "-c", "sleep 30 & child=$!; echo $child; exit 0"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        child_pid = int(process.stdout.readline())
        process.wait()

        def child_is_live() -> bool:
            stat_fields = recorder_module._recording_process_stat_fields(child_pid)
            return stat_fields is not None and stat_fields[0] not in {"Z", "X", "x"}

        try:
            self.assertTrue(child_is_live())
            self.assertTrue(recorder_module._terminate_recorder_process_group(process))
            deadline = time.monotonic() + 2
            while child_is_live() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertFalse(child_is_live())
        finally:
            try:
                if child_is_live():
                    os.kill(child_pid, 9)
            except ProcessLookupError:
                pass
            process.communicate()

    def test_reaped_process_cleanup_kills_child_that_created_new_session(self) -> None:
        process = subprocess.Popen(
            [
                "python3",
                "-c",
                "import os,time; read_fd,write_fd=os.pipe(); child=os.fork(); "
                "(os.close(read_fd), os.setsid(), os.write(write_fd, str(os.getpid()).encode()), "
                "os.close(write_fd), time.sleep(30)) if child == 0 else "
                "(os.close(write_fd), print(os.read(read_fd, 32).decode(), flush=True), "
                "os.close(read_fd), time.sleep(0.1))",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        child_pid = int(process.stdout.readline())
        process.wait()

        def child_is_live() -> bool:
            stat_fields = recorder_module._recording_process_stat_fields(child_pid)
            return stat_fields is not None and stat_fields[0] not in {"Z", "X", "x"}

        try:
            self.assertTrue(child_is_live())
            self.assertTrue(recorder_module._terminate_recorder_process_group(process))
            self.assertFalse(child_is_live())
        finally:
            try:
                if child_is_live():
                    os.kill(child_pid, 9)
            except ProcessLookupError:
                pass
            process.communicate()

    def test_live_process_group_cleanup_kills_same_session_child_process_group(self) -> None:
        process = subprocess.Popen(
            [
                "python3",
                "-c",
                "import os,time; child=os.fork(); (os.setpgid(0,0) if child == 0 else print(child, flush=True)); time.sleep(30)",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        child_pid = int(process.stdout.readline())

        def child_is_live() -> bool:
            stat_fields = recorder_module._recording_process_stat_fields(child_pid)
            return stat_fields is not None and stat_fields[0] not in {"Z", "X", "x"}

        try:
            self.assertTrue(child_is_live())
            self.assertFalse(recorder_module._terminate_recorder_process_group(process))
            process.wait(timeout=2)
            deadline = time.monotonic() + 2
            while child_is_live() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertFalse(child_is_live())
        finally:
            try:
                if child_is_live():
                    os.kill(child_pid, 9)
            except ProcessLookupError:
                pass
            if process.poll() is None:
                process.kill()
            process.communicate()

    def test_timeout_cleanup_does_not_claim_unknown_session_group_complete(self) -> None:
        process = mock.Mock()
        process.pid = 1234
        process.poll.return_value = None

        with (
            mock.patch.object(recorder_module, "process_group_has_live_processes", return_value=None),
            mock.patch.object(recorder_module.os, "killpg") as mocked_killpg,
        ):
            self.assertFalse(recorder_module._terminate_recorder_process_group(process))

        mocked_killpg.assert_called_once_with(1234, recorder_module.signal.SIGKILL)

    def test_bounded_communicate_preserves_timeout_when_cleanup_is_interrupted(self) -> None:
        process = mock.Mock()
        timeout = subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=1)
        process.communicate.side_effect = timeout
        cleanup_error = KeyboardInterrupt("cleanup interrupted")

        with mock.patch.object(recorder_module, "_reap_timed_out_recorder_process", side_effect=cleanup_error):
            with self.assertRaises(subprocess.TimeoutExpired) as raised:
                recorder_module._communicate_recorder_process_bounded(
                    process,
                    timeout=1,
                    process_name="ffmpeg",
                )

        self.assertIs(raised.exception, timeout)
        self.assertIn("cleanup interrupted", "\n".join(timeout.__notes__))

    def test_run_ffmpeg_cleans_descendant_when_leader_exits_with_pipe_open(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            child_pid_path = Path(tmp) / "child.pid"
            command = [
                "/bin/sh",
                "-c",
                f"sleep 30 & child=$!; echo $child > {child_pid_path}; exit 0",
            ]
            with self.assertRaisesRegex(OSError, "bounded output capture failed"):
                recorder_module._run_ffmpeg_bounded(command, timeout=2, pass_fds=())

            child_pid = int(child_pid_path.read_text(encoding="utf-8").strip())

            def child_is_live() -> bool:
                stat_fields = recorder_module._recording_process_stat_fields(child_pid)
                return stat_fields is not None and stat_fields[0] not in {"Z", "X", "x"}

            try:
                self.assertFalse(child_is_live())
            finally:
                try:
                    os.kill(child_pid, 9)
                except ProcessLookupError:
                    pass

    def test_run_ffmpeg_cleans_reparented_new_session_child_with_pipe_open(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            child_pid_path = Path(tmp) / "child.pid"
            code = (
                "import os,time; child=os.fork(); "
                f"(os.setsid(), open({str(child_pid_path)!r}, 'w').write(str(os.getpid())), time.sleep(30)) "
                "if child == 0 else os._exit(0)"
            )
            with self.assertRaisesRegex(OSError, "bounded output capture failed"):
                recorder_module._run_ffmpeg_bounded(
                    ["/usr/bin/python3", "-c", code],
                    timeout=2,
                    pass_fds=(),
                )

            child_pid = int(child_pid_path.read_text(encoding="ascii"))
            stat_fields = recorder_module._recording_process_stat_fields(child_pid)
            self.assertTrue(stat_fields is None or stat_fields[0] in {"Z", "X", "x"})
            try:
                if stat_fields is not None and stat_fields[0] not in {"Z", "X", "x"}:
                    os.kill(child_pid, 9)
            except ProcessLookupError:
                pass

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

    def test_read_recording_level_ignores_large_riff_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            metadata = b"\x7f" * 600
            audio.write_bytes(
                b"RIFF"
                + b"\x00\x00\x00\x00"
                + b"WAVE"
                + b"JUNK"
                + len(metadata).to_bytes(4, "little")
                + metadata
                + b"data"
                + (8).to_bytes(4, "little")
                + b"\x00" * 8
            )

            level = read_recording_level(audio)

        self.assertTrue(level.ok)
        self.assertEqual(level.percent, 0)
        self.assertEqual(level.samples, 4)
        self.assertEqual(level.detail, "silence")

    def test_read_recording_level_ignores_trailing_riff_chunk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            fmt = b"fmt " + (16).to_bytes(4, "little") + b"\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00"
            body = (
                b"WAVE"
                + fmt
                + b"data"
                + (4).to_bytes(4, "little")
                + b"\x00\x00\x00\x00"
                + b"JUNK"
                + (4).to_bytes(4, "little")
                + b"\xff\x7f\xff\x7f"
            )
            audio.write_bytes(b"RIFF" + len(body).to_bytes(4, "little") + body)

            level = read_recording_level(audio)

        self.assertTrue(level.ok)
        self.assertEqual(level.percent, 0)
        self.assertEqual(level.samples, 2)
        self.assertEqual(level.detail, "silence")

    def test_wav_data_offset_skips_data_text_inside_metadata_chunk(self) -> None:
        header = (
            b"RIFF"
            + b"\x00\x00\x00\x00"
            + b"WAVE"
            + b"JUNK"
            + (14).to_bytes(4, "little")
            + b"metadata data!"
            + b"data"
            + (4).to_bytes(4, "little")
            + b"\x00\x00\x00\x00"
        )

        self.assertEqual(_wav_data_offset(header), 42)

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
                with mock.patch("speed_of_cinnamon.recorder.subprocess.Popen", return_value=_FakePopen(completed)) as mocked_run:
                    result = detect_silent_recording(audio)

            self.assertEqual(result, SilenceDetectionResult(True, True, 2.0, 2.0, 0.0, 2.0, "silent recording"))
            argv = mocked_run.call_args.args[0]
        self.assertIn("-nostdin", argv)
        self.assertIn(f"silencedetect=noise={SILENCE_DETECT_NOISE}:d={SILENCE_DETECT_DURATION_SECONDS}", argv)
        input_path = argv[argv.index("-i") + 1]
        self.assertTrue(str(input_path).startswith("/proc/self/fd/"))
        self.assertEqual(mocked_run.call_args.kwargs["pass_fds"], (int(str(input_path).rsplit("/", 1)[-1]),))
        self.assertTrue(mocked_run.call_args.kwargs["start_new_session"])
        self.assertNotIsInstance(argv, str)

    def test_run_ffmpeg_timeout_kills_process_group_descendants(self) -> None:
        if not Path("/proc/self/stat").exists() or not hasattr(os, "killpg"):
            self.skipTest("process group inspection unavailable")

        with tempfile.TemporaryDirectory() as tmp:
            child_pid_path = Path(tmp) / "child.pid"
            command = [
                "/bin/sh",
                "-c",
                f"sleep 30 & child=$!; echo $child > {child_pid_path}; wait $child",
            ]
            with self.assertRaises(subprocess.TimeoutExpired):
                recorder_module._run_ffmpeg_bounded(command, timeout=1, pass_fds=())

            child_pid: int | None = None
            for _ in range(200):
                try:
                    child_pid = int(child_pid_path.read_text(encoding="utf-8").strip())
                    break
                except (FileNotFoundError, ValueError):
                    time.sleep(0.01)
            self.assertIsNotNone(child_pid)
            assert child_pid is not None

            for _ in range(200):
                stat_fields = recorder_module._recording_process_stat_fields(child_pid)
                if stat_fields is None or stat_fields[0] == "Z":
                    break
                time.sleep(0.01)
            else:
                os.kill(child_pid, 9)
                self.fail(f"ffmpeg descendant {child_pid} survived timeout")

    def test_detect_silent_recording_uses_file_backed_ffmpeg_output(self) -> None:
        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            command = args[0]
            stdout = kwargs["stdout"]
            stderr = kwargs["stderr"]
            self.assertNotEqual(stdout, subprocess.PIPE)
            self.assertNotEqual(stderr, subprocess.PIPE)
            stderr.write(
                b"Duration: 00:00:02.00, bitrate: 256 kb/s\n"
                b"[silencedetect @ 0x1] silence_start: 0\n"
            )
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "silent.wav"
            audio.write_bytes(b"RIFF" + b"\x00" * 44)
            with (
                mock.patch("speed_of_cinnamon.recorder._command_path", return_value="/usr/bin/ffmpeg"),
                mock.patch("speed_of_cinnamon.recorder.subprocess.Popen", side_effect=_popen_from_run(fake_run)),
            ):
                result = detect_silent_recording(audio)

        self.assertEqual(result, SilenceDetectionResult(True, True, 2.0, 2.0, 0.0, 2.0, "silent recording"))

    def test_detect_silent_recording_rejects_oversized_ffmpeg_output(self) -> None:
        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            command = args[0]
            stderr = kwargs["stderr"]
            stderr.write(b"x" * (recorder_module.MAX_FFMPEG_OUTPUT_BYTES + 1))
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "silent.wav"
            audio.write_bytes(b"RIFF" + b"\x00" * 44)
            with (
                mock.patch("speed_of_cinnamon.recorder._command_path", return_value="/usr/bin/ffmpeg"),
                mock.patch("speed_of_cinnamon.recorder.subprocess.Popen", side_effect=_popen_from_run(fake_run)),
            ):
                result = detect_silent_recording(audio)

        self.assertFalse(result.analyzed)
        self.assertFalse(result.silent)
        self.assertIn("exceeded safe output limit", result.detail)

    def test_run_ffmpeg_bounds_live_diagnostic_output(self) -> None:
        with mock.patch.object(recorder_module, "MAX_FFMPEG_OUTPUT_BYTES", 64):
            with self.assertRaisesRegex(RecorderError, "stdout exceeded safe output limit"):
                recorder_module._run_ffmpeg_bounded(
                    ["/bin/sh", "-c", "printf '%0100000d' 0"],
                    timeout=2,
                    pass_fds=(),
                )

    def test_detect_silent_recording_rejects_nonfinite_ffmpeg_duration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "malformed-duration.wav"
            audio.write_bytes(b"RIFF" + b"\x00" * 44)
            completed = subprocess.CompletedProcess(
                ["ffmpeg"],
                0,
                stdout=b"",
                stderr=(b"Duration: 00:00:" + b"9" * 400),
            )
            with mock.patch("speed_of_cinnamon.recorder._command_path", return_value="/usr/bin/ffmpeg"):
                with mock.patch("speed_of_cinnamon.recorder.subprocess.Popen", return_value=_FakePopen(completed)):
                    result = detect_silent_recording(audio)

        self.assertFalse(result.analyzed)
        self.assertFalse(result.silent)
        self.assertEqual(result.detail, "ffmpeg duration was unavailable")

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
                with mock.patch("speed_of_cinnamon.recorder.subprocess.Popen", return_value=_FakePopen(completed)):
                    result = detect_silent_recording(audio)

        self.assertFalse(result.silent)
        self.assertEqual(result.leading_silence_seconds, 1.25)
        self.assertEqual(result.silence_seconds, 1.5)
        self.assertEqual(result.speech_seconds, 2.0)

    def test_parse_silence_seconds_ignores_nonfinite_intervals(self) -> None:
        huge = "9" * 400

        self.assertEqual(
            _parse_silence_seconds(
                f"silence_start: 0\nsilence_end: {huge} | silence_duration: {huge}",
                10.0,
            ),
            (0.0, 0.0),
        )

    def test_parse_silence_seconds_merges_overlapping_intervals(self) -> None:
        self.assertEqual(
            _parse_silence_seconds(
                "silence_start: 0\n"
                "silence_end: 5 | silence_duration: 5\n"
                "silence_start: 4\n"
                "silence_end: 9 | silence_duration: 5\n",
                10.0,
            ),
            (9.0, 9.0),
        )

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

    def test_trim_recording_leading_silence_streams_frames_in_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            self._write_wav(audio, [0] * 1600 + [12000] * (recorder_module.WAV_TRIM_CHUNK_FRAMES + 5))
            read_sizes: list[int] = []
            real_readframes = wave.Wave_read.readframes

            def record_readframes(handle: wave.Wave_read, frames: int) -> bytes:
                read_sizes.append(frames)
                return real_readframes(handle, frames)

            with mock.patch("wave.Wave_read.readframes", record_readframes):
                trimmed = trim_recording_leading_silence(audio, 0.1)
            try:
                with wave.open(str(trimmed), "rb") as handle:
                    frame_count = handle.getnframes()
            finally:
                trimmed.unlink(missing_ok=True)

        self.assertEqual(frame_count, recorder_module.WAV_TRIM_CHUNK_FRAMES + 5)
        self.assertIn(recorder_module.WAV_TRIM_CHUNK_FRAMES, read_sizes)
        self.assertTrue(all(size <= recorder_module.WAV_TRIM_CHUNK_FRAMES for size in read_sizes))

    def test_trim_recording_leading_silence_rejects_oversized_streamed_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            self._write_wav(audio, [0] * 1600 + [12000] * 1600)
            with mock.patch("speed_of_cinnamon.recorder.MAX_FFMPEG_ARTIFACT_BYTES", 4):
                with self.assertRaisesRegex(RecorderError, "exceeded safe artifact size limit"):
                    trim_recording_leading_silence(audio, 0.1)

            self.assertEqual([path.name for path in Path(tmp).glob("*trimmed*.wav")], [])

    def test_trim_recording_leading_silence_rejects_streamed_artifact_when_header_exceeds_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            self._write_wav(audio, [0] * 1600 + [12000] * 1600)
            with mock.patch("speed_of_cinnamon.recorder.MAX_FFMPEG_ARTIFACT_BYTES", 3201):
                with self.assertRaisesRegex(RecorderError, "exceeded safe artifact size limit"):
                    trim_recording_leading_silence(audio, 0.1)

            self.assertEqual([path.name for path in Path(tmp).glob("*trimmed*.wav")], [])

    def test_trim_recording_leading_silence_cleans_temp_when_initial_stat_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            self._write_wav(audio, [0] * 1600 + [12000] * 1600)
            real_fstat = os.fstat
            calls = 0

            def fail_initial_stat(fd: int) -> os.stat_result:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("temporary stat failed")
                return real_fstat(fd)

            with mock.patch.object(recorder_module.os, "fstat", side_effect=fail_initial_stat):
                with self.assertRaisesRegex(RecorderError, "failed to trim recording audio file"):
                    trim_recording_leading_silence(audio, 0.1)

            self.assertEqual([path.name for path in Path(tmp).glob("*trimmed*.wav")], [])

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

    def test_trim_recording_leading_silence_rejects_nonfinite_seconds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            self._write_wav(audio, [0, 12000])

            for value in (float("nan"), float("inf"), float("-inf"), 10**1000):
                with self.subTest(value=value):
                    with self.assertRaisesRegex(RecorderError, "leading silence seconds must be numeric"):
                        trim_recording_leading_silence(audio, value)

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

    def test_trim_recording_leading_silence_validates_path_before_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "real.wav"
            real.write_bytes(b"audio")
            link = Path(tmp) / "link.wav"
            link.symlink_to(real)

            with self.assertRaisesRegex(RecorderError, "recording artifact path must not pass through a symlink"):
                trim_recording_leading_silence(link, 0)

    def test_trim_recording_silence_converts_to_flac(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")

            with mock.patch("speed_of_cinnamon.recorder._command_path", return_value="/usr/bin/ffmpeg"):
                with mock.patch(
                    "speed_of_cinnamon.recorder.subprocess.Popen",
                    side_effect=_popen_from_run(lambda *args, **kwargs: self._ffmpeg_success_with_output(args[0])),
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
        self.assertIn("-fs", argv)
        self.assertEqual(argv[argv.index("-fs") + 1], str(recorder_module.MAX_FFMPEG_ARTIFACT_BYTES))
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

    @mock.patch("speed_of_cinnamon.recorder.os.open", wraps=os.open)
    def test_trim_recording_silence_opens_audio_leaf_relative_to_parent_fd(self, mocked_open: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")

            with mock.patch("speed_of_cinnamon.recorder._command_path", return_value="/usr/bin/ffmpeg"):
                with mock.patch(
                    "speed_of_cinnamon.recorder.subprocess.Popen",
                    side_effect=_popen_from_run(lambda *args, **kwargs: self._ffmpeg_success_with_output(args[0])),
                ):
                    trimmed = trim_recording_silence(audio)
                    trimmed.unlink(missing_ok=True)

        self.assertTrue(
            any(
                args[0] == audio.name
                and isinstance(args[1], int)
                and args[1] & os.O_NOFOLLOW
                and "dir_fd" in kwargs
                for args, kwargs in mocked_open.call_args_list
            )
        )

    @mock.patch("speed_of_cinnamon.recorder.os.open", wraps=os.open)
    def test_open_recording_artifact_leaf_adds_secure_open_flags(self, mocked_open: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            fd = recorder_module._open_recording_artifact_leaf(audio, os.O_RDONLY, field_name="recording audio file")
            os.close(fd)

        self.assertTrue(
            any(
                args[0] == audio.name
                and isinstance(args[1], int)
                and args[1] & os.O_NOFOLLOW
                and "dir_fd" in kwargs
                for args, kwargs in mocked_open.call_args_list
            )
        )

    def test_open_recording_artifact_leaf_does_not_mask_open_fd_on_parent_close_failure(self) -> None:
        real_close = os.close
        real_open = os.open
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            parent_fd = real_open(tmp, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                with (
                    mock.patch.object(
                        recorder_module,
                        "open_directory_without_following_symlinks",
                        return_value=parent_fd,
                    ),
                    mock.patch.object(recorder_module.os, "close", side_effect=OSError("close failed")),
                ):
                    fd = recorder_module._open_recording_artifact_leaf(
                        audio,
                        os.O_RDONLY,
                        field_name="recording audio file",
                    )
                try:
                    self.assertEqual(os.read(fd, 5), b"audio")
                finally:
                    real_close(fd)
            finally:
                real_close(parent_fd)

    def test_validate_private_recording_audio_file_does_not_mask_fd_close_failure(self) -> None:
        from speed_of_cinnamon import recorder as recorder_module

        real_close = os.close
        fd = os.open(os.devnull, os.O_RDONLY)
        try:
            with (
                mock.patch.object(
                    recorder_module,
                    "_open_private_recording_audio_file",
                    return_value=(Path("/tmp/sample.wav"), fd),
                ),
                mock.patch.object(recorder_module.os, "close", side_effect=OSError("close failed")),
            ):
                normalized = recorder_module._validate_private_recording_audio_file(
                    Path("/tmp/sample.wav"),
                    suffix=".wav",
                )
            self.assertEqual(normalized, Path("/tmp/sample.wav"))
        finally:
            real_close(fd)

    def test_unlink_recording_path_does_not_escape_parent_fd_close_failure(self) -> None:
        from speed_of_cinnamon import recorder as recorder_module

        real_close = os.close
        real_open = os.open
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.wav"
            path.write_bytes(b"audio")
            expected_stat = path.stat()
            parent_fd = real_open(tmp, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                with (
                    mock.patch.object(
                        recorder_module,
                        "ensure_directory_without_following_symlinks",
                        return_value=parent_fd,
                    ),
                    mock.patch.object(recorder_module.os, "close", side_effect=OSError("close failed")),
                ):
                    recorder_module._unlink_recording_path_if_same(path, expected_stat)
            finally:
                self.assertFalse(path.exists())
                real_close(parent_fd)

    def test_unlink_recording_path_rejects_hardlink_race(self) -> None:
        from speed_of_cinnamon import recorder as recorder_module

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "recording.tmp"
            alias = Path(tmp) / "recording-alias.tmp"
            path.write_bytes(b"audio")
            expected_stat = path.stat()
            os.link(path, alias)

            fd = os.open(path, os.O_RDONLY)
            try:
                self.assertFalse(recorder_module._recording_temp_path_matches_fd(path, fd))
            finally:
                os.close(fd)
            recorder_module._unlink_recording_path_if_same(path, expected_stat)

            self.assertTrue(path.exists())
            self.assertTrue(alias.exists())

    def test_unlink_recording_path_preserves_replacement_during_cleanup(self) -> None:
        from speed_of_cinnamon import recorder as recorder_module

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "recording.flac"
            replacement = Path(tmp) / "replacement.flac"
            path.write_bytes(b"owned audio")
            replacement.write_bytes(b"foreign audio")
            expected_stat = path.stat()
            real_stat = recorder_module.os.stat
            path_stat_calls = 0

            def stat_then_replace(
                name: object,
                *args: object,
                **kwargs: object,
            ) -> os.stat_result:
                nonlocal path_stat_calls
                result = real_stat(name, *args, **kwargs)
                if name == path.name and kwargs.get("dir_fd") is not None:
                    path_stat_calls += 1
                    if path_stat_calls == 1:
                        path.unlink()
                        replacement.replace(path)
                return result

            with mock.patch.object(recorder_module.os, "stat", side_effect=stat_then_replace):
                recorder_module._unlink_recording_path_if_same(path, expected_stat)

            self.assertEqual(path.read_bytes(), b"foreign audio")
            self.assertFalse(list(Path(tmp).glob("recording.flac.*.cleanup")))

    def test_inspect_recording_temp_file_preserves_result_on_fd_close_failure(self) -> None:
        from speed_of_cinnamon import recorder as recorder_module

        output_stat = os.stat(__file__)
        with (
            mock.patch.object(recorder_module.os, "fstat", return_value=output_stat),
            mock.patch.object(recorder_module, "_recording_temp_path_matches_fd", return_value=True),
            mock.patch.object(recorder_module.os, "close", side_effect=OSError("close failed")),
        ):
            result = recorder_module._inspect_and_close_recording_temp_file(
                Path(__file__),
                42,
                field_name="test temporary file",
            )

        self.assertEqual(result, (output_stat.st_size, True, output_stat))

    def test_private_recording_open_preserves_validation_error_on_fd_close_failure(self) -> None:
        from speed_of_cinnamon import recorder as recorder_module

        expected = recorder_module.RecorderError("not a private regular file")
        with (
            mock.patch.object(recorder_module, "_open_recording_artifact_leaf", return_value=42),
            mock.patch.object(recorder_module, "assert_fd_is_regular_private_file", side_effect=expected),
            mock.patch.object(recorder_module.os, "close", side_effect=OSError("close failed")),
        ):
            with self.assertRaises(recorder_module.RecorderError) as raised:
                recorder_module._open_private_recording_audio_file(
                    Path("/tmp/sample.wav"),
                    suffix=".wav",
                )
        self.assertIs(raised.exception, expected)

    def test_private_recording_open_closes_fd_when_validation_is_interrupted(self) -> None:
        from speed_of_cinnamon import recorder as recorder_module

        with (
            mock.patch.object(recorder_module, "_open_recording_artifact_leaf", return_value=42),
            mock.patch.object(
                recorder_module,
                "assert_fd_is_regular_private_file",
                side_effect=KeyboardInterrupt,
            ),
            mock.patch.object(recorder_module, "_close_fd_quietly") as mocked_close,
        ):
            with self.assertRaises(KeyboardInterrupt):
                recorder_module._open_private_recording_audio_file(
                    Path("/tmp/sample.wav"),
                    suffix=".wav",
                )

        mocked_close.assert_called_once_with(42)

    def test_read_recording_level_closes_fd_when_validation_is_interrupted(self) -> None:
        from speed_of_cinnamon import recorder as recorder_module

        audio_path = Path("/tmp/sample.wav")
        with (
            mock.patch.object(recorder_module, "validate_recording_path", return_value=audio_path),
            mock.patch.object(recorder_module, "_open_recording_artifact_leaf", return_value=42),
            mock.patch.object(
                recorder_module,
                "assert_fd_is_regular_private_file",
                side_effect=KeyboardInterrupt,
            ),
            mock.patch.object(recorder_module, "_close_fd_quietly") as mocked_close,
        ):
            with self.assertRaises(KeyboardInterrupt):
                recorder_module.read_recording_level(audio_path)

        mocked_close.assert_called_once_with(42)

    def test_read_recording_level_closes_fd_when_fdopen_is_interrupted(self) -> None:
        from speed_of_cinnamon import recorder as recorder_module

        audio_path = Path("/tmp/sample.wav")
        with (
            mock.patch.object(recorder_module, "validate_recording_path", return_value=audio_path),
            mock.patch.object(recorder_module, "_open_recording_artifact_leaf", return_value=42),
            mock.patch.object(recorder_module, "assert_fd_is_regular_private_file"),
            mock.patch.object(recorder_module.os, "fdopen", side_effect=KeyboardInterrupt),
            mock.patch.object(recorder_module, "_close_fd_quietly") as mocked_close,
        ):
            with self.assertRaises(KeyboardInterrupt):
                recorder_module.read_recording_level(audio_path)

        mocked_close.assert_called_once_with(42)

    def test_read_recording_level_preserves_read_interrupt_when_handle_close_fails(self) -> None:
        class _Handle:
            def fileno(self) -> int:
                return 42

            def read(self, _size: int = -1) -> bytes:
                raise KeyboardInterrupt("audio read interrupted")

            def close(self) -> None:
                raise OSError("audio close failed")

        audio_path = Path("/tmp/sample.wav")
        with (
            mock.patch.object(recorder_module, "validate_recording_path", return_value=audio_path),
            mock.patch.object(recorder_module, "_open_recording_artifact_leaf", return_value=42),
            mock.patch.object(recorder_module, "assert_fd_is_regular_private_file"),
            mock.patch.object(recorder_module.os, "fdopen", return_value=_Handle()),
            mock.patch.object(recorder_module.os, "fstat", return_value=mock.Mock(st_size=100)),
        ):
            with self.assertRaisesRegex(KeyboardInterrupt, "audio read interrupted") as caught:
                recorder_module.read_recording_level(audio_path)

        self.assertIn("recorder audio cleanup failed", "\n".join(caught.exception.__notes__))
        self.assertIn("audio close failed", "\n".join(caught.exception.__notes__))

    def test_silence_detection_preserves_result_on_audio_fd_close_failure(self) -> None:
        from speed_of_cinnamon import recorder as recorder_module

        audio_path = Path("/tmp/sample.wav")
        with (
            mock.patch.object(
                recorder_module,
                "_open_private_recording_audio_file",
                return_value=(audio_path, 42),
            ),
            mock.patch.object(
                recorder_module,
                "_command_path",
                side_effect=recorder_module.RecorderError("ffmpeg missing"),
            ),
            mock.patch.object(recorder_module.os, "close", side_effect=OSError("close failed")),
        ):
            result = recorder_module.detect_silent_recording(audio_path)

        self.assertFalse(result.analyzed)
        self.assertIn("ffmpeg missing", result.detail)

    def test_trim_recording_leading_silence_open_error_does_not_leak_audio_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "secret-sample.wav"
            self._write_wav(audio, [0, 12000, 12000])
            with mock.patch("speed_of_cinnamon.recorder.wave.open", side_effect=OSError(f"cannot open {audio}")):
                with self.assertRaisesRegex(RecorderError, "failed to trim recording audio file") as raised:
                    trim_recording_leading_silence(audio, 0.1)
            self.assertNotIn(str(audio), str(raised.exception))

    def test_trim_recording_silence_cleans_temp_on_interruption(self) -> None:
        from speed_of_cinnamon import recorder as recorder_module

        audio_path = Path("/probe/input.wav")
        temp_path = Path("/probe/.input.trimmed-test.flac")
        with (
            mock.patch.object(recorder_module, "_open_private_recording_audio_file", return_value=(audio_path, 11)),
            mock.patch.object(recorder_module, "_create_recording_temp_file", return_value=(42, temp_path)),
            mock.patch.object(recorder_module.os, "fstat", return_value=os.stat(__file__)),
            mock.patch.object(recorder_module, "_ffmpeg_output_path_for_fd", side_effect=["out", "in"]),
            mock.patch.object(recorder_module, "_command_path", return_value="/usr/bin/ffmpeg"),
            mock.patch.object(recorder_module, "_run_ffmpeg_bounded", side_effect=KeyboardInterrupt),
            mock.patch.object(recorder_module, "_cleanup_recording_temp_file") as mocked_cleanup,
            mock.patch.object(recorder_module, "_close_fd_quietly") as mocked_close,
        ):
            with self.assertRaises(KeyboardInterrupt):
                recorder_module.trim_recording_silence(audio_path)

        mocked_cleanup.assert_called_once_with(temp_path, 42)
        mocked_close.assert_called_once_with(11)

    def test_reencode_recording_cleans_temp_on_interruption(self) -> None:
        from speed_of_cinnamon import recorder as recorder_module

        audio_path = Path("/probe/input.wav")
        temp_path = Path("/probe/.input.encoded-test.flac")
        with (
            mock.patch.object(recorder_module, "_open_private_recording_audio_file", return_value=(audio_path, 11)),
            mock.patch.object(recorder_module, "_create_recording_temp_file", return_value=(42, temp_path)),
            mock.patch.object(recorder_module.os, "fstat", return_value=os.stat(__file__)),
            mock.patch.object(recorder_module, "_ffmpeg_output_path_for_fd", side_effect=["out", "in"]),
            mock.patch.object(recorder_module, "_command_path", return_value="/usr/bin/ffmpeg"),
            mock.patch.object(recorder_module, "_run_ffmpeg_bounded", side_effect=KeyboardInterrupt),
            mock.patch.object(recorder_module, "_cleanup_recording_temp_file") as mocked_cleanup,
            mock.patch.object(recorder_module, "_close_fd_quietly") as mocked_close,
        ):
            with self.assertRaises(KeyboardInterrupt):
                recorder_module.reencode_recording_to_flac(audio_path)

        mocked_cleanup.assert_called_once_with(temp_path, 42)
        mocked_close.assert_called_once_with(11)

    def test_trim_recording_silence_empty_output_does_not_leak_audio_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "secret-sample.wav"
            audio.write_bytes(b"audio")
            completed = subprocess.CompletedProcess(["ffmpeg"], 0, stdout=b"", stderr=b"")
            with mock.patch("speed_of_cinnamon.recorder._command_path", return_value="/usr/bin/ffmpeg"):
                with mock.patch("speed_of_cinnamon.recorder.subprocess.Popen", return_value=_FakePopen(completed)):
                    with self.assertRaisesRegex(RecorderError, "ffmpeg silence trimming produced empty output") as raised:
                        trim_recording_silence(audio)
            self.assertNotIn(str(audio), str(raised.exception))

    def test_trim_recording_silence_rejects_oversized_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")

            def oversized_output(command: list[str]) -> subprocess.CompletedProcess[bytes]:
                Path(command[-1]).write_bytes(b"x" * 4)
                return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

            with (
                mock.patch("speed_of_cinnamon.recorder.MAX_FFMPEG_ARTIFACT_BYTES", 4),
                mock.patch("speed_of_cinnamon.recorder._command_path", return_value="/usr/bin/ffmpeg"),
                mock.patch("speed_of_cinnamon.recorder.subprocess.Popen", side_effect=_popen_from_run(lambda *args, **kwargs: oversized_output(args[0]))),
            ):
                with self.assertRaisesRegex(RecorderError, "exceeded safe artifact size limit"):
                    trim_recording_silence(audio)
            self.assertEqual([path.name for path in Path(tmp).glob("*.flac")], [])

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

    def test_trim_recording_silence_rejects_nonfinite_duration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")

            for value in (float("nan"), float("inf"), float("-inf"), 10**1000):
                with self.subTest(value=value):
                    with self.assertRaisesRegex(RecorderError, "silence trim duration must be numeric"):
                        trim_recording_silence(audio, duration_seconds=value)

    def test_reencode_recording_to_flac_uses_ffmpeg_flac_encoder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            with mock.patch("speed_of_cinnamon.recorder._command_path", return_value="/usr/bin/ffmpeg"):
                with mock.patch(
                    "speed_of_cinnamon.recorder.subprocess.Popen",
                    side_effect=_popen_from_run(lambda *args, **kwargs: self._ffmpeg_success_with_output(args[0])),
                ) as mocked_run:
                    output = reencode_recording_to_flac(audio)
                    output_exists = output.exists()

        self.assertEqual(output.suffix, ".flac")
        self.assertTrue(output_exists)
        argv = mocked_run.call_args.args[0]
        self.assertIn("-c:a", argv)
        self.assertIn("flac", argv)
        self.assertIn("-f", argv)
        self.assertIn("-fs", argv)
        self.assertEqual(argv[argv.index("-fs") + 1], str(recorder_module.MAX_FFMPEG_ARTIFACT_BYTES))
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

    def test_reencode_recording_to_flac_empty_output_does_not_leak_audio_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "secret-sample.wav"
            audio.write_bytes(b"audio")
            completed = subprocess.CompletedProcess(["ffmpeg"], 0, stdout=b"", stderr=b"")
            with mock.patch("speed_of_cinnamon.recorder._command_path", return_value="/usr/bin/ffmpeg"):
                with mock.patch("speed_of_cinnamon.recorder.subprocess.Popen", return_value=_FakePopen(completed)):
                    with self.assertRaisesRegex(RecorderError, "ffmpeg FLAC conversion produced empty output") as raised:
                        reencode_recording_to_flac(audio)
            self.assertNotIn(str(audio), str(raised.exception))

    def test_audio_transforms_clean_temp_when_fd_stat_needs_proc_fallback(self) -> None:
        if not Path("/proc/self/fd").is_dir():
            self.skipTest("/proc/self/fd unavailable")

        for transform in (trim_recording_silence, reencode_recording_to_flac):
            with self.subTest(transform=transform.__name__), tempfile.TemporaryDirectory() as tmp:
                audio = Path(tmp) / "sample.wav"
                audio.write_bytes(b"audio")
                target_fd: int | None = None
                real_create = recorder_module._create_recording_temp_file
                real_fstat = recorder_module.os.fstat

                def create_temp(*args: object, **kwargs: object) -> tuple[int, Path]:
                    nonlocal target_fd
                    target_fd, temp_path = real_create(*args, **kwargs)
                    return target_fd, temp_path

                def fail_temp_fstat(fd: int) -> os.stat_result:
                    if target_fd is not None and fd == target_fd:
                        raise OSError("temporary stat failed")
                    return real_fstat(fd)

                with (
                    mock.patch.object(recorder_module, "_create_recording_temp_file", side_effect=create_temp),
                    mock.patch.object(recorder_module, "_command_path", return_value="/usr/bin/ffmpeg"),
                    mock.patch.object(
                        recorder_module.subprocess,
                        "Popen",
                        return_value=_FakePopen(subprocess.CompletedProcess(["ffmpeg"], 0, b"", b"")),
                    ),
                    mock.patch.object(recorder_module.os, "fstat", side_effect=fail_temp_fstat),
                ):
                    with self.assertRaisesRegex(RecorderError, "produced empty output"):
                        transform(audio)

                self.assertEqual(list(Path(tmp).glob("*.flac")), [])

    def test_reencode_recording_to_flac_rejects_oversized_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")

            def oversized_output(command: list[str]) -> subprocess.CompletedProcess[bytes]:
                Path(command[-1]).write_bytes(b"x" * 4)
                return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

            with (
                mock.patch("speed_of_cinnamon.recorder.MAX_FFMPEG_ARTIFACT_BYTES", 4),
                mock.patch("speed_of_cinnamon.recorder._command_path", return_value="/usr/bin/ffmpeg"),
                mock.patch("speed_of_cinnamon.recorder.subprocess.Popen", side_effect=_popen_from_run(lambda *args, **kwargs: oversized_output(args[0]))),
            ):
                with self.assertRaisesRegex(RecorderError, "exceeded safe artifact size limit"):
                    reencode_recording_to_flac(audio)
            self.assertEqual([path.name for path in Path(tmp).glob("*.flac")], [])

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
                    "speed_of_cinnamon.recorder.subprocess.Popen",
                    side_effect=_popen_from_run(lambda *args, **kwargs: self._ffmpeg_success_with_output(args[0])),
                ):
                    with mock.patch.object(recorder_module, "_recording_temp_path_matches_fd", return_value=False):
                        with self.assertRaisesRegex(RecorderError, "temporary file was replaced"):
                            trim_recording_silence(audio)

    def test_trim_recording_silence_removes_temp_file_when_inspection_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")

            def fail_inspection(path: Path, fd: int, *, field_name: str) -> None:
                os.close(fd)
                raise RecorderError("inspection failed")

            with (
                mock.patch("speed_of_cinnamon.recorder._command_path", return_value="/usr/bin/ffmpeg"),
                mock.patch(
                    "speed_of_cinnamon.recorder.subprocess.Popen",
                    side_effect=_popen_from_run(lambda *args, **kwargs: self._ffmpeg_success_with_output(args[0])),
                ),
                mock.patch.object(
                    recorder_module,
                    "_inspect_and_close_recording_temp_file",
                    side_effect=fail_inspection,
                ),
            ):
                with self.assertRaisesRegex(RecorderError, "inspection failed"):
                    trim_recording_silence(audio)

            self.assertEqual(list(Path(tmp).glob("*.flac")), [])

    def test_trim_recording_silence_removes_temp_file_when_inspection_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")

            def fail_inspection(path: Path, fd: int, *, field_name: str) -> None:
                os.close(fd)
                raise KeyboardInterrupt("inspection interrupted")

            with (
                mock.patch("speed_of_cinnamon.recorder._command_path", return_value="/usr/bin/ffmpeg"),
                mock.patch(
                    "speed_of_cinnamon.recorder.subprocess.Popen",
                    side_effect=_popen_from_run(lambda *args, **kwargs: self._ffmpeg_success_with_output(args[0])),
                ),
                mock.patch.object(
                    recorder_module,
                    "_inspect_and_close_recording_temp_file",
                    side_effect=fail_inspection,
                ),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    trim_recording_silence(audio)

            self.assertEqual(list(Path(tmp).glob("*.flac")), [])

    def test_reencode_recording_to_flac_removes_temp_file_when_inspection_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")

            def fail_inspection(path: Path, fd: int, *, field_name: str) -> None:
                os.close(fd)
                raise RecorderError("inspection failed")

            with (
                mock.patch("speed_of_cinnamon.recorder._command_path", return_value="/usr/bin/ffmpeg"),
                mock.patch(
                    "speed_of_cinnamon.recorder.subprocess.Popen",
                    side_effect=_popen_from_run(lambda *args, **kwargs: self._ffmpeg_success_with_output(args[0])),
                ),
                mock.patch.object(
                    recorder_module,
                    "_inspect_and_close_recording_temp_file",
                    side_effect=fail_inspection,
                ),
            ):
                with self.assertRaisesRegex(RecorderError, "inspection failed"):
                    reencode_recording_to_flac(audio)

            self.assertEqual(list(Path(tmp).glob("*.flac")), [])

    def test_reencode_recording_to_flac_removes_temp_file_when_inspection_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")

            def fail_inspection(path: Path, fd: int, *, field_name: str) -> None:
                os.close(fd)
                raise KeyboardInterrupt("inspection interrupted")

            with (
                mock.patch("speed_of_cinnamon.recorder._command_path", return_value="/usr/bin/ffmpeg"),
                mock.patch(
                    "speed_of_cinnamon.recorder.subprocess.Popen",
                    side_effect=_popen_from_run(lambda *args, **kwargs: self._ffmpeg_success_with_output(args[0])),
                ),
                mock.patch.object(
                    recorder_module,
                    "_inspect_and_close_recording_temp_file",
                    side_effect=fail_inspection,
                ),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    reencode_recording_to_flac(audio)

            self.assertEqual(list(Path(tmp).glob("*.flac")), [])

    def test_trim_recording_silence_reports_ffmpeg_error_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            failed = subprocess.CompletedProcess(["ffmpeg"], 1, stdout=b"", stderr=b"bad audio")
            with mock.patch("speed_of_cinnamon.recorder._command_path", return_value="/usr/bin/ffmpeg"):
                with mock.patch("speed_of_cinnamon.recorder.subprocess.Popen", return_value=_FakePopen(failed)):
                    with self.assertRaisesRegex(RecorderError, "bad audio"):
                        trim_recording_silence(audio)

    def test_sanitize_ffmpeg_error_strips_ansi_control_chars(self) -> None:
        detail = recorder_module._sanitize_ffmpeg_error_detail("\x1b[31mboom\x1b[0m\x07")

        self.assertEqual(detail, "boom")
        self.assertNotIn("\x1b", detail)
        self.assertNotIn("\x07", detail)

    def test_trim_recording_silence_redacts_ffmpeg_path_error_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "secret-sample.wav"
            audio.write_bytes(b"audio")
            failed = subprocess.CompletedProcess(["ffmpeg"], 1, stdout=b"", stderr=f"failed {audio} token secret".encode())
            with mock.patch("speed_of_cinnamon.recorder._command_path", return_value="/usr/bin/ffmpeg"):
                with mock.patch("speed_of_cinnamon.recorder.subprocess.Popen", return_value=_FakePopen(failed)):
                    with self.assertRaisesRegex(RecorderError, "\\[redacted ffmpeg error\\]") as raised:
                        trim_recording_silence(audio)
            self.assertNotIn(str(audio), str(raised.exception))
            self.assertNotIn("secret", str(raised.exception))

    def test_trim_recording_silence_rejects_oversized_ffmpeg_output(self) -> None:
        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            command = args[0]
            stderr = kwargs["stderr"]
            stderr.write(b"x" * (recorder_module.MAX_FFMPEG_OUTPUT_BYTES + 1))
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            with (
                mock.patch("speed_of_cinnamon.recorder._command_path", return_value="/usr/bin/ffmpeg"),
                mock.patch("speed_of_cinnamon.recorder.subprocess.Popen", side_effect=_popen_from_run(fake_run)),
            ):
                with self.assertRaisesRegex(RecorderError, "exceeded safe output limit"):
                    trim_recording_silence(audio)

    def test_reencode_recording_to_flac_reports_ffmpeg_error_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            failed = subprocess.CompletedProcess(["ffmpeg"], 1, stdout=b"", stderr=b"encoder failed")
            with mock.patch("speed_of_cinnamon.recorder._command_path", return_value="/usr/bin/ffmpeg"):
                with mock.patch("speed_of_cinnamon.recorder.subprocess.Popen", return_value=_FakePopen(failed)):
                    with self.assertRaisesRegex(RecorderError, "encoder failed"):
                        reencode_recording_to_flac(audio)

    def test_reencode_recording_to_flac_redacts_ffmpeg_path_error_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "secret-sample.wav"
            audio.write_bytes(b"audio")
            failed = subprocess.CompletedProcess(["ffmpeg"], 1, stdout=b"", stderr=f"failed {audio} token secret".encode())
            with mock.patch("speed_of_cinnamon.recorder._command_path", return_value="/usr/bin/ffmpeg"):
                with mock.patch("speed_of_cinnamon.recorder.subprocess.Popen", return_value=_FakePopen(failed)):
                    with self.assertRaisesRegex(RecorderError, "\\[redacted ffmpeg error\\]") as raised:
                        reencode_recording_to_flac(audio)
            self.assertNotIn(str(audio), str(raised.exception))
            self.assertNotIn("secret", str(raised.exception))

    def test_audio_transforms_clean_temp_when_ffmpeg_error_is_invalid_utf8(self) -> None:
        def invalid_error(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            stderr = kwargs["stderr"]
            assert hasattr(stderr, "write")
            stderr.write(b"\xff")
            return subprocess.CompletedProcess(args[0], 1, stdout=b"", stderr=b"")

        for transform in (trim_recording_silence, reencode_recording_to_flac):
            with self.subTest(transform=transform.__name__), tempfile.TemporaryDirectory() as tmp:
                audio = Path(tmp) / "sample.wav"
                audio.write_bytes(b"audio")
                with (
                    mock.patch("speed_of_cinnamon.recorder._command_path", return_value="/usr/bin/ffmpeg"),
                    mock.patch(
                        "speed_of_cinnamon.recorder.subprocess.Popen",
                        side_effect=_popen_from_run(invalid_error),
                    ),
                ):
                    with self.assertRaisesRegex(RecorderError, "invalid UTF-8"):
                        transform(audio)

                self.assertEqual(list(Path(tmp).glob("*.flac")), [])

    def test_detect_silent_recording_fails_open_when_ffmpeg_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"RIFF" + b"\x00" * 44)
            with mock.patch("speed_of_cinnamon.recorder._command_path", side_effect=RecorderError("ffmpeg missing")):
                result = detect_silent_recording(audio)

        self.assertFalse(result.analyzed)
        self.assertFalse(result.silent)
        self.assertIn("ffmpeg missing", result.detail)

    def test_detect_silent_recording_closes_audio_fd_when_ffmpeg_lookup_is_interrupted(self) -> None:
        audio_path = Path("/probe/sample.wav")
        with (
            mock.patch.object(
                recorder_module,
                "_open_private_recording_audio_file",
                return_value=(audio_path, 42),
            ),
            mock.patch.object(recorder_module, "_command_path", side_effect=KeyboardInterrupt("lookup interrupted")),
            mock.patch.object(recorder_module, "_close_fd_quietly") as mocked_close,
        ):
            with self.assertRaisesRegex(KeyboardInterrupt, "lookup interrupted"):
                recorder_module.detect_silent_recording(audio_path)

        mocked_close.assert_called_once_with(42)

    def test_detect_silent_recording_redacts_subprocess_exception_detail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "secret-token-recording.wav"
            audio.write_bytes(b"RIFF" + b"\x00" * 44)
            error = OSError(f"failed {audio} token secret")
            with (
                mock.patch("speed_of_cinnamon.recorder._command_path", return_value="/usr/bin/ffmpeg"),
                mock.patch("speed_of_cinnamon.recorder.subprocess.Popen", side_effect=error),
            ):
                result = detect_silent_recording(audio)

        self.assertFalse(result.analyzed)
        self.assertIn("[redacted ffmpeg error]", result.detail)
        self.assertNotIn(str(audio), result.detail)
        self.assertNotIn("secret", result.detail)

    def test_detect_silent_recording_fails_closed_on_invalid_ffmpeg_utf8(self) -> None:
        def invalid_output(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            stderr = kwargs["stderr"]
            assert hasattr(stderr, "write")
            stderr.write(b"\xff")
            return subprocess.CompletedProcess(args[0], 0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"RIFF" + b"\x00" * 44)
            with (
                mock.patch("speed_of_cinnamon.recorder._command_path", return_value="/usr/bin/ffmpeg"),
                mock.patch(
                    "speed_of_cinnamon.recorder.subprocess.Popen",
                    side_effect=_popen_from_run(invalid_output),
                ),
            ):
                result = detect_silent_recording(audio)

        self.assertFalse(result.analyzed)
        self.assertFalse(result.silent)
        self.assertIn("invalid UTF-8", result.detail)

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
                args[0] == audio.name
                and isinstance(args[1], int)
                and args[1] & os.O_NOFOLLOW
                and "dir_fd" in kwargs
                for args, kwargs in mocked_open.call_args_list
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

    def test_read_recording_level_missing_file_does_not_leak_audio_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "secret-sample.wav"

            with self.assertRaisesRegex(RecorderError, "recording audio file is not readable") as raised:
                read_recording_level(audio)
            self.assertNotIn(str(audio), str(raised.exception))

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

    def test_completed_output_bytes_rejects_malformed_utf8(self) -> None:
        with self.assertRaisesRegex(RecorderError, "invalid UTF-8"):
            _completed_output_bytes("\ud800", field_name="stdout")

    def test_decode_ffmpeg_output_rejects_malformed_utf8(self) -> None:
        with self.assertRaisesRegex(RecorderError, "invalid UTF-8"):
            _decode_ffmpeg_output(b"ok\xff")

    def test_decode_ffmpeg_output_trims_str_and_bytes(self) -> None:
        self.assertEqual(_decode_ffmpeg_output("  bad audio  "), "bad audio")
        self.assertEqual(_decode_ffmpeg_output(b"  bad audio  "), "bad audio")

    def test_decode_ffmpeg_output_returns_empty_for_non_text(self) -> None:
        self.assertEqual(_decode_ffmpeg_output(123), "")

    def test_assert_valid_input_device_rejects_malformed_utf8(self) -> None:
        with self.assertRaisesRegex(RecorderError, "invalid UTF-8"):
            _assert_valid_input_device("\ud800")

    def test_validate_recording_path_rejects_malformed_utf8(self) -> None:
        with self.assertRaisesRegex(RecorderError, "invalid UTF-8"):
            validate_recording_path(Path("\ud800.wav"), suffix=".wav")

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

    def test_start_recorder_rejects_non_text_recorder_name(self) -> None:
        command = RecorderCommand(name=None, argv=["true"])  # type: ignore[arg-type]
        with self.assertRaisesRegex(RecorderError, "recorder name must be text"):
            start_recorder(command, Path("/tmp/session.log"))

    def test_start_recorder_rejects_non_sequence_recorder_arguments(self) -> None:
        command = RecorderCommand(name="noop", argv=1)  # type: ignore[arg-type]
        with self.assertRaisesRegex(RecorderError, "arguments must be a sequence"):
            start_recorder(command, Path("/tmp/session.log"))

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
        self.assertIsInstance(mocked_popen.call_args.kwargs["stdout"], recorder_module._RecorderLogCapture)
        self.assertEqual(mocked_popen.call_args.kwargs["stderr"], subprocess.STDOUT)
        mocked_popen.call_args.kwargs["stdout"].finish()

    def test_recorder_log_capture_caps_output_and_preserves_existing_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "session.log"
            log_path.write_bytes(b"start")
            with log_path.open("ab") as log_file:
                capture = recorder_module._RecorderLogCapture(log_file, initial_size=5, max_bytes=8)
                os.write(capture.fileno(), b"0123456789" * 100)
                capture.finish()

            self.assertEqual(log_path.read_bytes(), b"start012")

    def test_recorder_log_capture_completes_partial_sink_writes(self) -> None:
        class PartialOutput:
            def __init__(self) -> None:
                self.payload = bytearray()

            def write(self, payload: bytes) -> int:
                self.payload.extend(payload[:1])
                return 1

            def flush(self) -> None:
                return None

            def close(self) -> None:
                return None

        output_file = PartialOutput()
        capture = recorder_module._RecorderLogCapture(output_file, initial_size=0, max_bytes=3)  # type: ignore[arg-type]
        os.write(capture.fileno(), b"abc")
        capture.finish()

        self.assertEqual(bytes(output_file.payload), b"abc")

    def test_cleanup_recording_temp_file_uses_proc_fd_stat_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp) / "sample.trimmed.flac"
            fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT, 0o600)
            with mock.patch.object(recorder_module.os, "fstat", side_effect=OSError("fstat failed")):
                recorder_module._cleanup_recording_temp_file(temp_path, fd)

            self.assertFalse(temp_path.exists())

    def test_bounded_output_capture_setup_cleans_first_capture_when_second_fails(self) -> None:
        first_capture = mock.Mock()
        with mock.patch(
            "speed_of_cinnamon.recorder._BoundedOutputCapture",
            side_effect=[first_capture, OSError("second capture failed")],
        ):
            with self.assertRaisesRegex(OSError, "second capture failed"):
                recorder_module._create_bounded_output_captures(
                    object(),  # type: ignore[arg-type]
                    object(),  # type: ignore[arg-type]
                    128,
                )

        first_capture.finish.assert_called_once_with()

    def test_start_recorder_bounds_real_backend_output(self) -> None:
        command = RecorderCommand(
            name="python3",
            argv=["python3", "-c", "import sys; sys.stderr.write('x' * 1000000)"],
        )
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "session.log"
            with (
                mock.patch.object(recorder_module, "MAX_RECORDER_LOG_BYTES", 64),
                mock.patch.object(recorder_module, "_command_path", return_value=sys.executable),
            ):
                process = start_recorder(command, log_path)
                self.assertEqual(process.wait(timeout=5), 0)

            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and log_path.stat().st_size < 64:
                time.sleep(0.01)
            self.assertLessEqual(log_path.stat().st_size, 64)

    def test_start_recorder_wraps_log_fdopen_value_error(self) -> None:
        command = RecorderCommand(name="noop", argv=["true"])
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "session.log"
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.recorder.shutil.which", return_value="/usr/bin/true"),
                mock.patch("speed_of_cinnamon.recorder.os.fdopen", side_effect=ValueError("bad fd")),
            ):
                with self.assertRaisesRegex(RecorderError, "failed to open recorder log file"):
                    start_recorder(command, log_path)

            self.assertFalse(log_path.exists())

    def test_start_recorder_cleans_log_when_log_fdopen_is_interrupted(self) -> None:
        command = RecorderCommand(name="noop", argv=["true"])
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "session.log"
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.recorder.os.fdopen", side_effect=KeyboardInterrupt("fdopen interrupted")),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    start_recorder(command, log_path)

            self.assertFalse(log_path.exists())

    def test_start_recorder_does_not_mask_started_process_when_log_close_fails(self) -> None:
        command = RecorderCommand(name="noop", argv=["true"])
        log_file = mock.Mock()
        log_file.fileno.return_value = 11
        log_file.close.side_effect = OSError("log flush failed")
        process = object()
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "session.log"
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.recorder._open_recorder_log_file", return_value=(log_file, False)),
                mock.patch("speed_of_cinnamon.recorder.shutil.which", return_value="/usr/bin/true"),
                mock.patch("speed_of_cinnamon.recorder.subprocess.Popen", return_value=process) as mocked_popen,
                mock.patch("speed_of_cinnamon.recorder.os.fchmod"),
            ):
                self.assertIs(start_recorder(command, log_path), process)
                mocked_capture = mocked_popen.call_args.kwargs["stdout"]
                mocked_capture.finish()

        log_file.close.assert_called_once()

    def test_start_recorder_does_not_mask_started_process_when_log_close_is_interrupted(self) -> None:
        command = RecorderCommand(name="noop", argv=["true"])
        log_file = mock.Mock()
        log_file.fileno.return_value = 11
        log_file.close.side_effect = KeyboardInterrupt
        process = object()
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "session.log"
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.recorder._open_recorder_log_file", return_value=(log_file, False)),
                mock.patch("speed_of_cinnamon.recorder.shutil.which", return_value="/usr/bin/true"),
                mock.patch("speed_of_cinnamon.recorder.subprocess.Popen", return_value=process) as mocked_popen,
                mock.patch("speed_of_cinnamon.recorder.os.fchmod"),
            ):
                self.assertIs(start_recorder(command, log_path), process)
                mocked_capture = mocked_popen.call_args.kwargs["stdout"]
                mocked_capture.finish()

        log_file.close.assert_called_once()

    def test_start_recorder_terminates_process_when_capture_writer_close_fails(self) -> None:
        command = RecorderCommand(name="noop", argv=["true"])
        process = mock.Mock()
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.recorder.shutil.which", return_value="/usr/bin/true"),
                mock.patch("speed_of_cinnamon.recorder.subprocess.Popen", return_value=process),
                mock.patch.object(
                    recorder_module._RecorderLogCapture,
                    "close_writer",
                    side_effect=OSError("writer close failed"),
                ),
                mock.patch.object(
                    recorder_module,
                    "_terminate_recorder_process_group",
                    return_value=True,
                ) as mocked_terminate,
            ):
                with self.assertRaisesRegex(RecorderError, "failed to start noop"):
                    start_recorder(command, Path(tmp) / "session.log")

        mocked_terminate.assert_called_once_with(process)

    def test_read_recording_level_closes_descriptor_when_fdopen_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio_path = Path(tmp) / "sample.wav"
            audio_path.write_bytes(b"0" * 128)
            fd = os.open(audio_path, os.O_RDONLY)
            try:
                with (
                    mock.patch("speed_of_cinnamon.recorder._open_recording_artifact_leaf", return_value=fd),
                    mock.patch("speed_of_cinnamon.recorder.os.fdopen", side_effect=ValueError("bad audio fd")),
                ):
                    with self.assertRaisesRegex(RecorderError, "recording audio file is not readable"):
                        read_recording_level(audio_path)

                with self.assertRaises(OSError):
                    os.fstat(fd)
            finally:
                try:
                    os.close(fd)
                except OSError:
                    pass

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

    def test_filtered_environment_skips_unencodable_environment_values(self) -> None:
        from speed_of_cinnamon.recorder import _filtered_environment as recorder_filtered_environment

        with mock.patch.object(recorder_module.os, "environ", {"HOME": "bad\ud800"}):
            env = recorder_filtered_environment()

        self.assertNotIn("HOME", env)
        self.assertIn("PATH", env)

    def test_filtered_environment_rejects_unencodable_base_values(self) -> None:
        with self.assertRaisesRegex(RecorderError, "environment value contains invalid UTF-8"):
            recorder_module._filtered_environment(base={"SAFE_KEY": "bad\ud800"})

    def test_filtered_environment_rejects_unencodable_base_keys(self) -> None:
        with self.assertRaisesRegex(RecorderError, "environment key contains invalid UTF-8"):
            recorder_module._filtered_environment(base={"BAD\ud800": "value"})

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

    def test_recording_temp_creation_does_not_mask_parent_fd_close_failure(self) -> None:
        from speed_of_cinnamon import recorder as recorder_module

        real_close = os.close
        real_open = os.open
        with tempfile.TemporaryDirectory() as tmp:
            audio_path = Path(tmp) / "sample.wav"
            parent_fd = real_open(tmp, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                with (
                    mock.patch.object(
                        recorder_module,
                        "ensure_directory_without_following_symlinks",
                        return_value=parent_fd,
                    ),
                    mock.patch.object(recorder_module.os, "close", side_effect=OSError("close failed")),
                ):
                    fd, temp_path = recorder_module._create_recording_temp_file(
                        audio_path,
                        marker="test",
                        suffix=".wav",
                    )
                self.assertTrue(temp_path.exists())
            finally:
                real_close(fd)
                temp_path.unlink()
                real_close(parent_fd)

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

    def test_open_recorder_log_removes_created_file_when_validation_fails(self) -> None:
        from speed_of_cinnamon import recorder as recorder_module

        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "session.log"
            with mock.patch.object(
                recorder_module,
                "assert_fd_is_regular_private_file",
                side_effect=RuntimeError("validation failed"),
            ):
                with self.assertRaisesRegex(RecorderError, "validation failed"):
                    recorder_module._open_recorder_log_file(log_path)

            self.assertFalse(log_path.exists())

    def test_open_recorder_log_removes_created_file_when_fdopen_fails(self) -> None:
        from speed_of_cinnamon import recorder as recorder_module

        for failure in (OSError("fdopen failed"), ValueError("fdopen failed")):
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as tmp:
                log_path = Path(tmp) / "session.log"
                with mock.patch.object(recorder_module.os, "fdopen", side_effect=failure):
                    with self.assertRaisesRegex(RecorderError, "failed to open recorder log file"):
                        recorder_module._open_recorder_log_file(log_path)

                self.assertFalse(log_path.exists())

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

    def test_start_recorder_rejects_log_leaf_rename_swap_before_open(self) -> None:
        command = RecorderCommand(name="noop", argv=["true"])
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            original = base / "session.log"
            original.write_bytes(b"original")
            foreign = base / "foreign.log"
            foreign.write_bytes(b"foreign")
            swapped = False
            real_open = os.open

            def swapping_open(path: object, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
                nonlocal swapped
                if path == original.name and not swapped and not (flags & os.O_CREAT):
                    original.unlink()
                    os.link(foreign, original)
                    swapped = True
                return real_open(path, flags, mode, dir_fd=dir_fd)

            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.recorder.os.open", side_effect=swapping_open),
                mock.patch("speed_of_cinnamon.recorder.shutil.which", return_value="/usr/bin/true"),
            ):
                with self.assertRaisesRegex(RecorderError, "recorder log file must not be hardlinked"):
                    start_recorder(command, original)

            self.assertTrue(swapped)
            self.assertEqual(foreign.read_bytes(), b"foreign")

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
                with self.assertRaisesRegex(RecorderError, "failed to open recorder log file") as raised:
                    start_recorder(command, log_path)

            self.assertFalse((real / "session.log").exists())
            self.assertNotIn(str(log_path), str(raised.exception))

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
            self.assertFalse((Path(tmp) / "session.log").exists())

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

    @mock.patch("speed_of_cinnamon.recorder.subprocess.Popen", side_effect=ValueError("invalid process argument"))
    def test_start_recorder_wraps_process_argument_value_error(self, mocked_popen: mock.Mock) -> None:
        command = RecorderCommand(name="noop", argv=["true"])
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "session.log"
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}):
                with self.assertRaisesRegex(RecorderError, "failed to start noop"):
                    start_recorder(command, log_path)
            self.assertFalse(log_path.exists())
        mocked_popen.assert_called_once()

    def test_start_recorder_cleans_up_log_file_when_start_is_interrupted(self) -> None:
        command = RecorderCommand(name="noop", argv=["true"])
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "session.log"
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.recorder.shutil.which", return_value="/usr/bin/true"),
                mock.patch(
                    "speed_of_cinnamon.recorder.subprocess.Popen",
                    side_effect=KeyboardInterrupt("process start interrupted"),
                ),
            ):
                with self.assertRaises(KeyboardInterrupt) as context:
                    start_recorder(command, log_path)

            self.assertFalse(log_path.exists())

        self.assertEqual(str(context.exception), "process start interrupted")

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
                with self.assertRaisesRegex(RecorderError, "recorder log file must not be hardlinked"):
                    start_recorder(command, log_path)

            self.assertTrue(log_path.exists())
            self.assertEqual(log_path.read_text(encoding="utf-8"), "existing")

    @mock.patch("speed_of_cinnamon.recorder.subprocess.Popen", side_effect=OSError("boom"))
    def test_start_recorder_keeps_existing_log_file_when_start_fails(self, mocked_popen: mock.Mock) -> None:
        command = RecorderCommand(name="noop", argv=["true"])
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "session.log"
            log_path.write_text("previous content", encoding="utf-8")
            log_path.chmod(0o600)
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
            log_path.chmod(0o600)
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

    def test_unlink_recorder_log_preserves_replacement_during_cleanup(self) -> None:
        from speed_of_cinnamon import recorder as recorder_module

        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "session.log"
            replacement = Path(tmp) / "replacement.log"
            log_path.write_bytes(b"owned log")
            replacement.write_bytes(b"foreign log")
            expected_stat = log_path.stat()
            real_stat = recorder_module.os.stat
            log_stat_calls = 0

            def stat_then_replace(
                name: object,
                *args: object,
                **kwargs: object,
            ) -> os.stat_result:
                nonlocal log_stat_calls
                result = real_stat(name, *args, **kwargs)
                if name == log_path.name and kwargs.get("dir_fd") is not None:
                    log_stat_calls += 1
                    if log_stat_calls == 1:
                        log_path.unlink()
                        replacement.replace(log_path)
                return result

            with mock.patch.object(recorder_module.os, "stat", side_effect=stat_then_replace):
                recorder_module._unlink_recorder_log_if_same(log_path, expected_stat)

            self.assertEqual(log_path.read_bytes(), b"foreign log")
            self.assertFalse(list(Path(tmp).glob("session.log.*.cleanup")))

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
            mock.patch("speed_of_cinnamon.recorder.subprocess.Popen", side_effect=_popen_from_run(fake_run)),
        ):
            result = _run_pactl_command(("pactl", "get-default-source"), required=False)

        self.assertEqual(result, "default")
        self.assertEqual(calls[0][0], "/usr/bin/pactl")

    def test_run_pactl_command_sanitizes_error_controls(self) -> None:
        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            command = args[0] if args else kwargs["args"]
            stderr = kwargs["stderr"]
            stderr.write(b"\x1b[31mboom\x1b[0m\x07")
            return subprocess.CompletedProcess(command, 1, stdout=b"", stderr=b"")

        with (
            mock.patch("speed_of_cinnamon.recorder.shutil.which", return_value="/usr/bin/pactl"),
            mock.patch("speed_of_cinnamon.recorder.subprocess.Popen", side_effect=_popen_from_run(fake_run)),
        ):
            with self.assertRaisesRegex(RecorderError, "boom") as raised:
                _run_pactl_command(["pactl", "list"], required=True)

        self.assertNotIn("\x1b", str(raised.exception))
        self.assertNotIn("\x07", str(raised.exception))

    def test_run_pactl_bounds_live_output(self) -> None:
        with (
            mock.patch.object(recorder_module, "MAX_PACTL_OUTPUT_CHARS", 64),
            mock.patch.object(recorder_module, "_command_path", return_value="/bin/sh"),
        ):
            with self.assertRaisesRegex(RecorderError, "pactl command output exceeded 64 bytes"):
                recorder_module._run_pactl_command(
                    ["pactl", "-c", "printf '%0100000d' 0"],
                    required=False,
                )

    def test_run_pactl_command_redacts_sensitive_error_detail(self) -> None:
        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            command = args[0] if args else kwargs["args"]
            stderr = kwargs["stderr"]
            stderr.write(b"failed /tmp/secret token")
            return subprocess.CompletedProcess(command, 1, stdout=b"", stderr=b"")

        with (
            mock.patch("speed_of_cinnamon.recorder.shutil.which", return_value="/usr/bin/pactl"),
            mock.patch("speed_of_cinnamon.recorder.subprocess.Popen", side_effect=_popen_from_run(fake_run)),
        ):
            with self.assertRaisesRegex(RecorderError, "\\[redacted pactl error\\]") as raised:
                _run_pactl_command(["pactl", "list", "sources"], required=True)

        self.assertNotIn("/tmp/secret", str(raised.exception))
        self.assertNotIn("token", str(raised.exception))

    def test_run_pactl_command_does_not_include_argv_on_timeout(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.recorder.shutil.which", return_value="/usr/bin/pactl"),
            mock.patch(
                "speed_of_cinnamon.recorder.subprocess.Popen",
                return_value=_TimeoutPopen(["pactl", "secret-token"]),
            ),
        ):
            with self.assertRaisesRegex(RecorderError, "pactl command timed out") as raised:
                _run_pactl_command(["pactl", "secret-token"], required=True)

        self.assertNotIn("secret-token", str(raised.exception))

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
            mock.patch("speed_of_cinnamon.recorder.subprocess.Popen", side_effect=_popen_from_run(fake_run)) as mocked_popen,
        ):
            _run_pactl_command(["pactl"], required=False)

        self.assertEqual(calls, [["/usr/bin/pactl"]])
        self.assertTrue(mocked_popen.call_args.kwargs["start_new_session"])

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
            mock.patch("speed_of_cinnamon.recorder.subprocess.Popen", side_effect=_popen_from_run(fake_run)),
        ):
            _run_pactl_command(["pactl"], required=True)

        self.assertNotIn("LD_PRELOAD", captured_env)
        self.assertNotIn("PYTHONPATH", captured_env)
        self.assertEqual(captured_env["XDG_RUNTIME_DIR"], "/run/user/1000")
        self.assertEqual(captured_env["PULSE_SERVER"], "unix:/run/user/1000/pulse/native")
        self.assertEqual(captured_env["PATH"], "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")

    def test_run_pactl_command_timeout_kills_process_group_descendants(self) -> None:
        if not Path("/proc/self/stat").exists() or not hasattr(os, "killpg"):
            self.skipTest("process group inspection unavailable")

        with tempfile.TemporaryDirectory() as tmp:
            child_pid_path = Path(tmp) / "child.pid"
            command = [
                "pactl",
                "-c",
                f"sleep 30 & child=$!; echo $child > {child_pid_path}; wait $child",
            ]
            with (
                mock.patch.object(recorder_module, "_command_path", return_value="/bin/sh"),
                mock.patch.object(recorder_module, "MAX_PACTL_TIMEOUT_SECONDS", 1),
            ):
                with self.assertRaisesRegex(RecorderError, "pactl command timed out"):
                    recorder_module._run_pactl_command(command, required=False)

            child_pid: int | None = None
            for _ in range(200):
                try:
                    child_pid = int(child_pid_path.read_text(encoding="utf-8").strip())
                    break
                except (FileNotFoundError, ValueError):
                    time.sleep(0.01)
            self.assertIsNotNone(child_pid)
            assert child_pid is not None

            for _ in range(200):
                stat_fields = recorder_module._recording_process_stat_fields(child_pid)
                if stat_fields is None or stat_fields[0] == "Z":
                    break
                time.sleep(0.01)
            else:
                os.kill(child_pid, 9)
                self.fail(f"pactl descendant {child_pid} survived timeout")

    def test_run_pactl_cleans_descendant_when_leader_exits_with_pipe_open(self) -> None:
        if not Path("/proc/self/stat").exists() or not hasattr(os, "killpg"):
            self.skipTest("process group inspection unavailable")

        with tempfile.TemporaryDirectory() as tmp:
            child_pid_path = Path(tmp) / "child.pid"
            command = [
                "pactl",
                "-c",
                f"sleep 30 & child=$!; echo $child > {child_pid_path}; exit 0",
            ]
            with mock.patch.object(recorder_module, "_command_path", return_value="/bin/sh"):
                with self.assertRaisesRegex(RecorderError, "pactl command failed: bounded output capture failed"):
                    recorder_module._run_pactl_command(command, required=False)

            child_pid = int(child_pid_path.read_text(encoding="utf-8").strip())
            for _ in range(200):
                stat_fields = recorder_module._recording_process_stat_fields(child_pid)
                if stat_fields is None or stat_fields[0] in {"Z", "X", "x"}:
                    break
                time.sleep(0.01)
            else:
                try:
                    os.kill(child_pid, 9)
                except ProcessLookupError:
                    pass
                self.fail(f"pactl descendant {child_pid} survived capture cleanup")

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

    def test_parse_pactl_sources_rejects_control_char_sources(self) -> None:
        poisoned_sources = """Source #12
\tState: RUNNING
\tName: alsa_input.poisoned
\tDescription: Poisoned \x1b[31mMicrophone
\tDriver: PipeWire
\tMonitor of Sink: n/a

Source #13
\tState: SUSPENDED
\tName: alsa_input.clean
\tDescription: Clean Microphone
\tDriver: PipeWire
\tMonitor of Sink: n/a
"""

        sources = parse_pactl_sources(poisoned_sources, include_monitors=True)

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].id, "13")
        self.assertEqual(sources[0].name, "alsa_input.clean")
        self.assertEqual(sources[0].description, "Clean Microphone")

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
            mock.patch("speed_of_cinnamon.recorder.subprocess.Popen", side_effect=_popen_from_run(fake_run)),
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

    def test_stop_process_wraps_pid_range_errors(self) -> None:
        with mock.patch("speed_of_cinnamon.recorder.os.getpgid", side_effect=OverflowError("pid out of range")):
            with self.assertRaisesRegex(RecorderError, "failed to inspect recorder process"):
                stop_process(10**100, timeout_seconds=0.1, expected_process_identity="owner-identity")

    def test_stop_process_rejects_non_positive_timeout(self) -> None:
        with self.assertRaisesRegex(RecorderError, "timeout_seconds must be positive"):
            stop_process(1234, timeout_seconds=0)

    def test_stop_process_rejects_non_numeric_timeout(self) -> None:
        with self.assertRaisesRegex(RecorderError, "timeout_seconds must be numeric"):
            stop_process(1234, timeout_seconds="5")  # type: ignore[arg-type]

    def test_stop_process_rejects_infinite_timeout(self) -> None:
        for value in (float("inf"), 10**1000):
            with self.subTest(value=value):
                with self.assertRaisesRegex(RecorderError, "timeout_seconds must be finite"):
                    stop_process(1234, timeout_seconds=value)

    def test_stop_process_rejects_unboundedly_large_timeout(self) -> None:
        with mock.patch("speed_of_cinnamon.recorder._run_kill") as mocked_kill:
            with self.assertRaisesRegex(RecorderError, "timeout_seconds exceeds safe limit"):
                stop_process(
                    1234,
                    timeout_seconds=recorder_module.MAX_PROCESS_STOP_TIMEOUT_SECONDS + 1,
                    expected_process_identity="owner-identity",
                )

        mocked_kill.assert_not_called()

    def test_stop_process_rejects_missing_kill_command(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.recorder.os.getpgid", return_value=1234),
            mock.patch("speed_of_cinnamon.recorder._recording_process_identity_for_pid", return_value="owner-identity"),
            mock.patch("speed_of_cinnamon.recorder.subprocess.run", side_effect=OSError("missing")),
        ):
            with self.assertRaisesRegex(RecorderError, "failed to run kill command"):
                stop_process(1234, timeout_seconds=0.1, expected_process_identity="owner-identity")

    def test_stop_process_rejects_invalid_expected_process_identity(self) -> None:
        with mock.patch("speed_of_cinnamon.recorder.os.getpgid", return_value=1234):
            with self.assertRaisesRegex(RecorderError, "expected_process_identity must be text"):
                stop_process(1234, expected_process_identity=1234)  # type: ignore[arg-type]

    def test_stop_process_requires_expected_process_identity_by_default(self) -> None:
        with mock.patch("speed_of_cinnamon.recorder._run_kill") as mocked_kill:
            with self.assertRaisesRegex(RecorderError, "expected_process_identity is required"):
                stop_process(1234, timeout_seconds=0.1)

        mocked_kill.assert_not_called()

    def test_stop_process_rejects_kill_timeout(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.recorder.os.getpgid", return_value=1234),
            mock.patch("speed_of_cinnamon.recorder._recording_process_identity_for_pid", return_value="owner-identity"),
            mock.patch("speed_of_cinnamon.recorder.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="kill", timeout=1)),
        ):
            with self.assertRaisesRegex(RecorderError, "kill command timed out"):
                stop_process(1234, timeout_seconds=0.1, expected_process_identity="owner-identity")

    def test_stop_process_aborts_if_expected_identity_changes(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.recorder.os.getpgid", return_value=999),
            mock.patch("speed_of_cinnamon.recorder._recording_process_identity_for_pid", side_effect=["owner-identity", "foreign-identity"]) as mocked_identity,
            mock.patch("speed_of_cinnamon.recorder.os.kill", return_value=None),
            mock.patch("speed_of_cinnamon.recorder.time.monotonic", side_effect=[0.0, 0.0, 0.2]),
            mock.patch("speed_of_cinnamon.recorder.time.sleep"),
            mock.patch("speed_of_cinnamon.recorder._run_kill") as mocked_kill,
        ):
            result = stop_process(1234, timeout_seconds=0.1, expected_process_identity="owner-identity")

        self.assertFalse(result)
        self.assertEqual(mocked_identity.call_count, 2)
        self.assertEqual(mocked_kill.call_args_list[0].args[0], ["kill", "-INT", "--", "1234"])

    def test_stop_process_aborts_group_stop_when_leader_identity_changes_after_signal(self) -> None:
        identity_calls = 0

        def changing_identity(_pid: int) -> str:
            nonlocal identity_calls
            identity_calls += 1
            return "owner-identity" if identity_calls == 1 else "foreign-identity"

        with (
            mock.patch("speed_of_cinnamon.recorder.os.getpgid", return_value=1234),
            mock.patch("speed_of_cinnamon.recorder._recording_process_identity_for_pid", side_effect=changing_identity),
            mock.patch("speed_of_cinnamon.recorder.os.kill", return_value=None),
            mock.patch("speed_of_cinnamon.recorder.process_group_has_live_processes", return_value=True),
            mock.patch("speed_of_cinnamon.recorder.time.monotonic", side_effect=[0.0, 0.0, 0.2]),
            mock.patch("speed_of_cinnamon.recorder.time.sleep"),
            mock.patch("speed_of_cinnamon.recorder._run_kill") as mocked_kill,
        ):
            result = stop_process(1234, timeout_seconds=0.1, expected_process_identity="owner-identity")

        self.assertFalse(result)
        self.assertEqual(mocked_kill.call_args_list[0].args[0], ["kill", "-INT", "--", "-1234"])
        self.assertEqual(mocked_kill.call_count, 1)

    def test_stop_process_does_not_signal_if_expected_identity_already_mismatches(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.recorder.os.getpgid", return_value=1234),
            mock.patch("speed_of_cinnamon.recorder._recording_process_identity_for_pid", return_value="foreign-identity"),
            mock.patch("speed_of_cinnamon.recorder._run_kill") as mocked_kill,
        ):
            result = stop_process(1234, timeout_seconds=0.1, expected_process_identity="owner-identity")

        self.assertFalse(result)
        mocked_kill.assert_not_called()

    def test_stop_process_succeeds_only_when_identity_matches(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.recorder.os.getpgid", return_value=1234),
            mock.patch(
                "speed_of_cinnamon.recorder._recording_process_identity_for_pid",
                side_effect=["owner-identity", None],
            ) as mocked_identity,
            mock.patch("speed_of_cinnamon.recorder.os.kill", side_effect=ProcessLookupError) as mocked_os_kill,
            mock.patch(
                "speed_of_cinnamon.recorder._run_kill",
            ) as mocked_kill,
        ):
            result = stop_process(1234, timeout_seconds=0.1, expected_process_identity="owner-identity")

        self.assertTrue(result)
        self.assertEqual(mocked_identity.call_count, 1)
        mocked_os_kill.assert_called_once_with(-1234, 0)
        self.assertEqual(mocked_kill.call_args_list[0].args[0], ["kill", "-INT", "--", "-1234"])

    def test_stop_process_signals_recorder_process_group(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.recorder.os.getpgid", return_value=1234),
            mock.patch("speed_of_cinnamon.recorder._recording_process_identity_for_pid", return_value="owner-identity"),
            mock.patch("speed_of_cinnamon.recorder.os.kill", return_value=None),
            mock.patch("speed_of_cinnamon.recorder.process_group_has_live_processes", return_value=True),
            mock.patch("speed_of_cinnamon.recorder.time.monotonic", side_effect=[0.0, 0.0, 0.2]),
            mock.patch("speed_of_cinnamon.recorder.time.sleep"),
            mock.patch("speed_of_cinnamon.recorder._run_kill") as mocked_kill,
        ):
            result = stop_process(1234, timeout_seconds=0.1, expected_process_identity="owner-identity")

        self.assertFalse(result)
        self.assertEqual(mocked_kill.call_args_list[0].args[0], ["kill", "-INT", "--", "-1234"])
        self.assertEqual(mocked_kill.call_args_list[1].args[0], ["kill", "-TERM", "--", "-1234"])
        self.assertEqual(mocked_kill.call_args_list[2].args[0], ["kill", "-KILL", "--", "-1234"])

    def test_process_group_scan_ignores_disappeared_unrelated_process(self) -> None:
        entries = (Path("/proc/100"), Path("/proc/200"))
        stat_results = [None, ["Z", "1", "1234"]]
        with (
            mock.patch("speed_of_cinnamon.recorder.Path.iterdir", return_value=entries),
            mock.patch(
                "speed_of_cinnamon.recorder._recording_process_stat_fields",
                side_effect=stat_results,
            ),
            mock.patch(
                "speed_of_cinnamon.recorder.os.getpgid",
                side_effect=ProcessLookupError,
            ),
        ):
            result = recorder_module.process_group_has_live_processes(1234)

        self.assertFalse(result)

    def test_process_group_scan_reports_empty_group_as_stopped(self) -> None:
        with mock.patch("speed_of_cinnamon.recorder.Path.iterdir", return_value=()):
            result = recorder_module.process_group_has_live_processes(1234)

        self.assertFalse(result)

    def test_process_group_scan_fails_closed_for_same_session_different_group(self) -> None:
        entries = (Path("/proc/100"),)
        with (
            mock.patch("speed_of_cinnamon.recorder.Path.iterdir", return_value=entries),
            mock.patch(
                "speed_of_cinnamon.recorder._recording_process_stat_fields",
                return_value=["S", "1", "9999", "1234"],
            ),
        ):
            self.assertIsNone(recorder_module.process_group_has_live_processes(1234))
            self.assertIsNone(recorder_module._process_group_has_recorder_session(1234))

    def test_stop_process_signals_live_same_session_process_groups(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.recorder.os.getpgid", return_value=4321),
            mock.patch("speed_of_cinnamon.recorder.os.getsid", return_value=1234),
            mock.patch("speed_of_cinnamon.recorder._recording_process_identity_for_pid", return_value="owner-identity"),
            mock.patch("speed_of_cinnamon.recorder.os.kill", return_value=None),
            mock.patch(
                "speed_of_cinnamon.recorder._same_session_process_group_ids",
                return_value={1234, 4321},
            ),
            mock.patch("speed_of_cinnamon.recorder._same_session_has_live_processes", return_value=True),
            mock.patch("speed_of_cinnamon.recorder.time.monotonic", side_effect=[0.0, 0.0, 0.2]),
            mock.patch("speed_of_cinnamon.recorder.time.sleep"),
            mock.patch("speed_of_cinnamon.recorder._run_kill") as mocked_kill,
        ):
            result = stop_process(1234, timeout_seconds=0.1, expected_process_identity="owner-identity")

        self.assertFalse(result)
        self.assertEqual(
            [call.args[0] for call in mocked_kill.call_args_list],
            [
                ["kill", "-INT", "--", "-4321"],
                ["kill", "-INT", "--", "-1234"],
                ["kill", "-TERM", "--", "-4321"],
                ["kill", "-TERM", "--", "-1234"],
                ["kill", "-KILL", "--", "-4321"],
                ["kill", "-KILL", "--", "-1234"],
            ],
        )

    def test_recording_process_stat_decode_errors_fail_closed(self) -> None:
        error = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid process name")
        with mock.patch.object(recorder_module.Path, "read_text", side_effect=error):
            self.assertIsNone(recorder_module._recording_process_stat_fields(1234))
            self.assertIsNone(recorder_module._recording_process_identity_for_pid(1234))

    def test_process_group_scan_ignores_foreign_session(self) -> None:
        entries = (Path("/proc/100"),)
        with (
            mock.patch("speed_of_cinnamon.recorder.Path.iterdir", return_value=entries),
            mock.patch(
                "speed_of_cinnamon.recorder._recording_process_stat_fields",
                return_value=["S", "1", "1234", "9999"],
            ),
        ):
            self.assertFalse(recorder_module.process_group_has_live_processes(1234))
            self.assertFalse(recorder_module._process_group_exists(1234))

    def test_process_group_exists_accepts_successful_kill_zero_fallback(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.recorder._process_group_has_recorder_session", return_value=False),
            mock.patch("speed_of_cinnamon.recorder.os.kill", return_value=None) as mocked_kill,
        ):
            self.assertTrue(recorder_module._process_group_exists(1234))

        mocked_kill.assert_called_once_with(-1234, 0)

    def test_stop_process_accepts_zombie_group_leader_as_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            process = start_recorder(
                RecorderCommand("sleep", ["sleep", "30"]),
                Path(tmp) / "session.log",
            )
            try:
                identity = recorder_module._recording_process_identity_for_pid(process.pid)
                self.assertIsNotNone(identity)
                self.assertTrue(stop_process(process.pid, timeout_seconds=1, expected_process_identity=identity))
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait()

    def test_stop_process_signals_pid_when_process_is_not_group_leader(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.recorder.os.getpgid", return_value=999),
            mock.patch("speed_of_cinnamon.recorder._recording_process_identity_for_pid", return_value="owner-identity"),
            mock.patch("speed_of_cinnamon.recorder.os.kill", return_value=None),
            mock.patch("speed_of_cinnamon.recorder.time.monotonic", side_effect=[0.0, 0.0, 0.2]),
            mock.patch("speed_of_cinnamon.recorder.time.sleep"),
            mock.patch("speed_of_cinnamon.recorder._run_kill") as mocked_kill,
        ):
            result = stop_process(1234, timeout_seconds=0.1, expected_process_identity="owner-identity")

        self.assertFalse(result)
        self.assertEqual(mocked_kill.call_args_list[0].args[0], ["kill", "-INT", "--", "1234"])
        self.assertEqual(mocked_kill.call_args_list[1].args[0], ["kill", "-TERM", "--", "1234"])
        self.assertEqual(mocked_kill.call_args_list[2].args[0], ["kill", "-KILL", "--", "1234"])

    def test_stop_process_does_not_treat_permission_denied_kill_zero_as_success(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.recorder.os.getpgid", return_value=1234),
            mock.patch("speed_of_cinnamon.recorder._recording_process_identity_for_pid", return_value="owner-identity"),
            mock.patch("speed_of_cinnamon.recorder.os.kill", side_effect=PermissionError("Operation not permitted")),
            mock.patch("speed_of_cinnamon.recorder.time.monotonic", side_effect=[0.0, 0.0, 0.2]),
            mock.patch("speed_of_cinnamon.recorder.time.sleep"),
            mock.patch("speed_of_cinnamon.recorder._run_kill") as mocked_kill,
        ):
            result = stop_process(1234, timeout_seconds=0.1, expected_process_identity="owner-identity")

        self.assertFalse(result)
        self.assertEqual(mocked_kill.call_args_list[0].args[0], ["kill", "-INT", "--", "-1234"])
        self.assertEqual(mocked_kill.call_args_list[1].args[0], ["kill", "-TERM", "--", "-1234"])
        self.assertEqual(mocked_kill.call_args_list[2].args[0], ["kill", "-KILL", "--", "-1234"])

    def test_stop_process_returns_when_process_is_already_gone(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.recorder.os.getpgid", side_effect=ProcessLookupError),
            mock.patch("speed_of_cinnamon.recorder._process_group_exists", return_value=False),
            mock.patch("speed_of_cinnamon.recorder._run_kill") as mocked_kill,
        ):
            result = stop_process(1234, timeout_seconds=0.1, expected_process_identity="owner-identity")

        self.assertTrue(result)
        mocked_kill.assert_not_called()

    def test_stop_process_fails_closed_when_reaped_group_presence_is_unknown(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.recorder.os.getpgid", side_effect=ProcessLookupError),
            mock.patch("speed_of_cinnamon.recorder._process_group_exists", return_value=None),
            mock.patch("speed_of_cinnamon.recorder._run_kill") as mocked_kill,
        ):
            result = stop_process(1234, timeout_seconds=0.1, expected_process_identity="owner-identity")

        self.assertFalse(result)
        mocked_kill.assert_not_called()

    def test_stop_process_does_not_assume_reaped_group_is_gone_on_permission_error(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.recorder.os.getpgid", side_effect=ProcessLookupError),
            mock.patch("speed_of_cinnamon.recorder.os.kill", side_effect=PermissionError("Operation not permitted")),
            mock.patch("speed_of_cinnamon.recorder._recording_process_identity_matches", return_value=False),
            mock.patch("speed_of_cinnamon.recorder._run_kill") as mocked_kill,
        ):
            result = stop_process(1234, timeout_seconds=0.1, expected_process_identity="owner-identity")

        self.assertFalse(result)
        mocked_kill.assert_not_called()

    def test_stop_process_does_not_kill_reused_zombie_pid(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.recorder.os.getpgid", side_effect=ProcessLookupError),
            mock.patch("speed_of_cinnamon.recorder._process_group_exists", return_value=True),
            mock.patch("speed_of_cinnamon.recorder._process_group_has_recorder_session", return_value=False),
            mock.patch("speed_of_cinnamon.recorder._recording_process_identity_for_pid", return_value=None),
            mock.patch("speed_of_cinnamon.recorder._process_is_gone", return_value=True),
            mock.patch("speed_of_cinnamon.recorder._run_kill") as mocked_kill,
        ):
            result = stop_process(1234, timeout_seconds=0.1, expected_process_identity="owner-identity")

        self.assertFalse(result)
        mocked_kill.assert_not_called()

    def test_stop_process_does_not_kill_present_zombie_when_identity_is_unknown(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.recorder.os.getpgid", return_value=1234),
            mock.patch("speed_of_cinnamon.recorder.os.kill", return_value=None),
            mock.patch("speed_of_cinnamon.recorder._recording_process_identity_for_pid", return_value=None),
            mock.patch("speed_of_cinnamon.recorder._process_is_gone", return_value=True),
            mock.patch("speed_of_cinnamon.recorder._process_group_exists", return_value=True),
            mock.patch("speed_of_cinnamon.recorder._process_group_has_recorder_session", return_value=True),
            mock.patch("speed_of_cinnamon.recorder._run_kill") as mocked_kill,
        ):
            result = stop_process(1234, timeout_seconds=0.1, expected_process_identity="owner-identity")

        self.assertFalse(result)
        mocked_kill.assert_not_called()

    def test_stop_process_cleans_group_after_leader_was_reaped(self) -> None:
        group_checks = 0

        def process_gone(target: str) -> bool:
            nonlocal group_checks
            if target == "1234":
                return True
            group_checks += 1
            return group_checks >= 4

        with (
            mock.patch("speed_of_cinnamon.recorder.os.getpgid", side_effect=ProcessLookupError),
            mock.patch("speed_of_cinnamon.recorder._process_group_exists", return_value=True) as mocked_group_exists,
            mock.patch("speed_of_cinnamon.recorder._process_group_has_recorder_session", return_value=True),
            mock.patch("speed_of_cinnamon.recorder._recording_process_identity_matches", return_value=False),
            mock.patch("speed_of_cinnamon.recorder._process_is_gone", side_effect=process_gone),
            mock.patch("speed_of_cinnamon.recorder.time.monotonic", side_effect=[0.0, 0.0, 0.2]),
            mock.patch("speed_of_cinnamon.recorder.time.sleep"),
            mock.patch("speed_of_cinnamon.recorder._run_kill") as mocked_kill,
        ):
            result = stop_process(1234, timeout_seconds=0.1, expected_process_identity="owner-identity")

        self.assertTrue(result)
        self.assertGreaterEqual(mocked_group_exists.call_count, 2)
        self.assertEqual(mocked_kill.call_args_list[0].args[0], ["kill", "-INT", "--", "-1234"])
        self.assertEqual(mocked_kill.call_args_list[1].args[0], ["kill", "-TERM", "--", "-1234"])
        self.assertEqual(mocked_kill.call_args_list[2].args[0], ["kill", "-KILL", "--", "-1234"])

    def test_stop_process_kills_live_descendant_that_created_new_session(self) -> None:
        process = subprocess.Popen(
            [
                "python3",
                "-c",
                "import os,time; read_fd,write_fd=os.pipe(); child=os.fork(); "
                "(os.close(read_fd), os.setsid(), os.write(write_fd, str(os.getpid()).encode()), "
                "os.close(write_fd), time.sleep(30)) if child == 0 else "
                "(os.close(write_fd), print(os.read(read_fd, 32).decode(), flush=True), "
                "os.close(read_fd), time.sleep(30))",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        child_pid = int(process.stdout.readline())
        identity = recorder_module._recording_process_identity_for_pid(process.pid)
        self.assertIsNotNone(identity)

        def child_is_live() -> bool:
            stat_fields = recorder_module._recording_process_stat_fields(child_pid)
            return stat_fields is not None and stat_fields[0] not in {"Z", "X", "x"}

        try:
            self.assertTrue(child_is_live())
            self.assertTrue(stop_process(process.pid, timeout_seconds=1, expected_process_identity=identity))
            self.assertFalse(child_is_live())
        finally:
            try:
                if child_is_live():
                    os.kill(child_pid, 9)
            except ProcessLookupError:
                pass
            if process.poll() is None:
                process.kill()
            process.communicate()


if __name__ == "__main__":
    unittest.main()
