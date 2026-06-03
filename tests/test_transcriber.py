# mypy: ignore-errors
from __future__ import annotations

import os
import io
import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from speed_of_cinnamon.transcriber import (
    TranscriberConfig,
    TranscriptionError,
    MAX_OPENAI_COMPATIBLE_API_KEY_CHARS,
    MAX_OPENAI_COMPATIBLE_MODEL_CHARS,
    MAX_AUDIO_FILE_BYTES,
    MAX_AUDIO_PATH_CHARS,
    MAX_TRANSCRIBER_TEXT_CHARS,
    _multipart_form_data,
    _read_file_head,
    _read_text_file,
    _assert_text_length,
    _contains_escaped_null,
    _validate_same_origin_redirect,
    _quote,
    _write_text_atomic,
    validate_audio_file,
    _run_limited_process,
    transcribe_with_openai_whisper,
    transcribe_with_whisper_cpp,
    transcribe_with_template,
    normalize_backend,
    render_command_template,
    resolve_whisper_cpp_command,
    resolve_transcriber,
    transcribe,
)
from speed_of_cinnamon.command_chain import CommandChainError
from speed_of_cinnamon.personalization import MAX_PERSONAL_CONTEXT_CHARS, MAX_VOCABULARY_CHARS


class TranscriberTest(unittest.TestCase):
    def test_template_quotes_placeholders(self) -> None:
        rendered = render_command_template(
            "tool --audio {audio} --lang {language} --text {text} --prompt {prompt}",
            Path("/tmp/with space/audio.wav"),
            "de",
            Path("/tmp/out text.txt"),
            "Use Cinnamon terms.",
            "PipeWire",
        )
        self.assertIn("'/tmp/with space/audio.wav'", rendered)
        self.assertIn("--lang de", rendered)
        self.assertIn("'/tmp/out text.txt'", rendered)
        self.assertIn("Use Cinnamon terms.", rendered)
        self.assertIn("PipeWire", rendered)

    def test_template_rejects_oversized_personal_context(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "personal context is too large"):
            render_command_template(
                "tool --prompt {prompt}",
                Path("/tmp/audio.wav"),
                "de",
                Path("/tmp/out.txt"),
                "x" * (MAX_PERSONAL_CONTEXT_CHARS + 1),
                "PipeWire",
            )

    def test_template_rejects_oversized_vocabulary(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "vocabulary is too large"):
            render_command_template(
                "tool --prompt {prompt}",
                Path("/tmp/audio.wav"),
                "de",
                Path("/tmp/out.txt"),
                "Use terms",
                "x" * (MAX_VOCABULARY_CHARS + 1),
            )

    @mock.patch("speed_of_cinnamon.transcriber.os.replace", side_effect=OSError("disk full"))
    def test_transcribe_rejects_transcript_write_failure(self, mocked_replace: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            with self.assertRaisesRegex(TranscriptionError, "failed to write transcript file"):
                transcribe(
                    audio,
                    "en",
                    text,
                    "printf hello",
                )
        mocked_replace.assert_called_once()

    def test_transcribe_rejects_symlinked_transcript_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            real_dir = Path(tmp) / "real-transcripts"
            real_dir.mkdir()
            link_dir = Path(tmp) / "link-transcripts"
            link_dir.symlink_to(real_dir, target_is_directory=True)
            with self.assertRaisesRegex(TranscriptionError, "transcript path must not pass through a symlink"):
                transcribe(
                    audio,
                    "en",
                    link_dir / "sample.txt",
                    "printf hello",
                )

    def test_write_text_atomic_sets_private_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "transcript.txt"
            _write_text_atomic(path, "private output")
            mode = path.stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)

    def test_template_supports_safe_chained_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            result = transcribe(
                audio,
                "en",
                text,
                "printf pre && printf post",
            )
        self.assertEqual(result, "post")

    def test_template_chain_passes_output_between_segments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            result = transcribe(
                audio,
                "en",
                text,
                "python3 -c 'import sys; print(\"pre\")' && python3 -c 'import sys; print(sys.stdin.read().strip())'",
            )
        self.assertEqual(result, "pre")

    def test_transcribe_rejects_foreign_language_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"

            with self.assertRaisesRegex(TranscriptionError, "outside configured language"):
                transcribe(
                    audio,
                    "en",
                    text,
                    "printf '[speaking in foreign language]'",
                )

    @mock.patch("speed_of_cinnamon.transcriber._read_text_file", side_effect=TranscriptionError("failed to read generated transcript: /tmp/sample.txt"))
    def test_template_read_error_is_hardened(self, _mocked_read: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            text.write_text("generated transcript", encoding="utf-8")
            with mock.patch("speed_of_cinnamon.transcriber.run_command_chain", return_value="generated transcript"):
                with self.assertRaisesRegex(TranscriptionError, "failed to read generated transcript"):
                    transcribe_with_template("{text}", audio, "en", text)

    def test_template_read_file_rejects_invalid_utf8(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            text.write_bytes(b"\xff")
            with mock.patch("speed_of_cinnamon.transcriber.run_command_chain", return_value="generated transcript"):
                with self.assertRaisesRegex(TranscriptionError, "failed to read generated transcript"):
                    transcribe_with_template("{text}", audio, "en", text)

    def test_template_read_file_rejects_escaped_x00(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            text.write_text("line\\\\x00end", encoding="utf-8")
            with mock.patch("speed_of_cinnamon.transcriber.run_command_chain", return_value="generated transcript"):
                with self.assertRaisesRegex(TranscriptionError, "failed to read generated transcript"):
                    transcribe_with_template("{text}", audio, "en", text)

    def test_read_file_head_rejects_invalid_utf8(self) -> None:
        with tempfile.TemporaryFile() as handle:
            handle.write(b"ok\xff")
            with self.assertRaisesRegex(TranscriptionError, "not valid UTF-8"):
                _read_file_head(handle, 10)

    def test_read_file_head_rejects_escaped_null(self) -> None:
        with tempfile.TemporaryFile() as handle:
            handle.write("ok\\x00end".encode("utf-8"))
            with self.assertRaisesRegex(TranscriptionError, "contains invalid null byte"):
                _read_file_head(handle, 10)

    def test_read_file_head_rejects_non_integer_max_chars(self) -> None:
        with tempfile.TemporaryFile() as handle:
            handle.write(b"ok")
            with self.assertRaisesRegex(TranscriptionError, "max_chars must be an integer"):
                _read_file_head(handle, 1.2)  # type: ignore[arg-type]

    def test_read_file_head_rejects_zero_max_chars(self) -> None:
        with tempfile.TemporaryFile() as handle:
            handle.write(b"ok")
            with self.assertRaisesRegex(TranscriptionError, "max_chars must be positive"):
                _read_file_head(handle, 0)

    def test_read_text_file_rejects_non_path(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "path must be a Path"):
            _read_text_file("sample.txt")  # type: ignore[arg-type]

    @mock.patch("speed_of_cinnamon.path_safety.os.open", wraps=os.open)
    def test_read_text_file_uses_secure_open_flags(self, mocked_open: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.txt"
            path.write_text("generated transcript", encoding="utf-8")
            text = _read_text_file(path)
        self.assertEqual(text, "generated transcript")
        self.assertTrue(
            any(
                args[0] == path.name
                and isinstance(args[1], int)
                and args[1] & os.O_NOFOLLOW
                and "dir_fd" in kwargs
                for args, kwargs in mocked_open.call_args_list
            )
        )

    def test_validate_audio_file_rejects_null_byte_path(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "invalid null byte"):
            validate_audio_file(Path("sample\x00.wav"))

    def test_validate_audio_file_rejects_escaped_null_path(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "invalid null byte"):
            validate_audio_file(Path("sample\\x00.wav"))

    def test_validate_audio_file_rejects_control_character(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "invalid control character"):
            validate_audio_file(Path("sample\n.wav"))

    def test_validate_audio_file_rejects_home_symlink_expansion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            real_home = Path(tmp) / "real-home"
            link_home = Path(tmp) / "link-home"
            real_home.mkdir()
            audio = real_home / "sample.wav"
            audio.write_bytes(b"audio")
            link_home.symlink_to(real_home, target_is_directory=True)
            with mock.patch.dict("os.environ", {"HOME": str(link_home)}):
                with self.assertRaisesRegex(TranscriptionError, "audio path must not pass through a symlink"):
                    validate_audio_file(Path("~/sample.wav"))

    def test_validate_audio_file_rejects_non_path(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "audio path must be a Path"):
            validate_audio_file("sample.wav")  # type: ignore[arg-type]

    def test_validate_audio_file_rejects_directory_without_extra_is_file_stat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio_dir = Path(tmp) / "sample.wav"
            audio_dir.mkdir()
            with mock.patch("pathlib.Path.is_file", side_effect=AssertionError("extra is_file stat")):
                with self.assertRaisesRegex(TranscriptionError, "audio path is not a regular file"):
                    validate_audio_file(audio_dir)

    def test_validate_audio_file_rejects_oversized_path_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ("😀" * ((MAX_AUDIO_PATH_CHARS // 4) + 1))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"audio")
            with self.assertRaisesRegex(TranscriptionError, "audio file path is too long"):
                validate_audio_file(path)

    def test_assert_text_length_rejects_non_text(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "must be text"):
            _assert_text_length(12, field_name="text")

    def test_assert_text_length_rejects_oversized_text_bytes(self) -> None:
        with mock.patch("speed_of_cinnamon.transcriber.MAX_TRANSCRIPT_TEXT_CHARS", 4):
            with self.assertRaisesRegex(TranscriptionError, "is too large"):
                _assert_text_length("😀😀", field_name="transcript")

    def test_contains_escaped_null_rejects_non_text(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "value must be text"):
            _contains_escaped_null(12)  # type: ignore[arg-type]

    def test_contains_escaped_null_rejects_bool(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "value must be text"):
            _contains_escaped_null(True)  # type: ignore[arg-type]

    def test_quote_rejects_non_text_value(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "value must be text"):
            _quote(123)  # type: ignore[arg-type]

    def test_normalize_backend_rejects_non_text(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "backend must be text"):
            normalize_backend(123)  # type: ignore[arg-type]

    def test_normalize_backend_rejects_bool(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "backend must be text"):
            normalize_backend(True)  # type: ignore[arg-type]

    def test_resolve_transcriber_rejects_non_config(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "config must be TranscriberConfig"):
            resolve_transcriber("not-config")  # type: ignore[arg-type]

    def test_template_rejects_oversized_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            with mock.patch("speed_of_cinnamon.transcriber.MAX_TRANSCRIPT_TEXT_CHARS", 4):
                with self.assertRaisesRegex(TranscriptionError, "transcript is too large"):
                    transcribe(
                        audio,
                        "en",
                        Path(tmp) / "sample.txt",
                        "python3 -c 'print(\"toolong\")'",
                    )

    def test_template_rejects_unsupported_shell_operators(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            with self.assertRaisesRegex(TranscriptionError, "unsupported shell operator"):
                transcribe(
                    audio,
                    "en",
                    Path(tmp) / "sample.txt",
                    "printf pre | cat",
                )

    def test_template_rejects_invalid_syntax(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            with self.assertRaisesRegex(TranscriptionError, "invalid transcriber command"):
                transcribe(
                    audio,
                    "en",
                    Path(tmp) / "sample.txt",
                    "printf 'unterminated",
                )

    def test_transcribe_rejects_oversized_command_template(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            with self.assertRaisesRegex(TranscriptionError, "command template is too large"):
                transcribe(
                    audio,
                    "en",
                    Path(tmp) / "sample.txt",
                    "😀" * ((MAX_TRANSCRIBER_TEXT_CHARS // 4) + 1),
                )

    def test_template_rejects_empty_command_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            with (
                mock.patch("speed_of_cinnamon.transcriber.split_command_chain", return_value=[]),
                mock.patch(
                    "speed_of_cinnamon.transcriber.run_command_chain",
                    side_effect=CommandChainError("transcriber command chain is empty"),
                ),
            ):
                with self.assertRaisesRegex(TranscriptionError, "command chain is empty"):
                    transcribe_with_template("printf ok", audio, "en", Path(tmp) / "sample.txt")

    def test_template_rejects_invalid_chain_limit_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            with (
                mock.patch("speed_of_cinnamon.transcriber.split_command_chain", return_value=[("cmd",)]),
                mock.patch(
                    "speed_of_cinnamon.transcriber.run_command_chain",
                    side_effect=CommandChainError("max_output_chars must be non-negative"),
                ),
            ):
                with self.assertRaisesRegex(TranscriptionError, "max_output_chars must be non-negative"):
                    transcribe_with_template("cmd", audio, "en", Path(tmp) / "sample.txt")

    def test_template_rejects_updated_chain_limit_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            with (
                mock.patch("speed_of_cinnamon.transcriber.split_command_chain", return_value=[("cmd",)]),
                mock.patch(
                    "speed_of_cinnamon.transcriber.run_command_chain",
                    side_effect=CommandChainError("max_output_chars must be positive"),
                ),
            ):
                with self.assertRaisesRegex(TranscriptionError, "max_output_chars must be positive"):
                    transcribe_with_template("cmd", audio, "en", Path(tmp) / "sample.txt")

    def test_template_reports_missing_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            with self.assertRaisesRegex(TranscriptionError, "path separators"):
                transcribe(
                    audio,
                    "en",
                    Path(tmp) / "sample.txt",
                    "/definitely/missing/command",
                )

    def test_openai_whisper_rejects_oversized_output(self) -> None:
        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            stdout_file = kwargs["stdout"]
            command = args[0] if args else kwargs["args"]
            stdout_file.write(b"x" * 5)
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            generated = Path(tmp) / "sample.txt"
            generated.write_text("hello", encoding="utf-8")

            with (
                mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper"),
                mock.patch("speed_of_cinnamon.transcriber.MAX_COMMAND_OUTPUT_CHARS", 4),
                mock.patch("speed_of_cinnamon.transcriber.subprocess.run", side_effect=fake_run),
            ):
                with self.assertRaisesRegex(TranscriptionError, "output exceeded"):
                    transcribe_with_openai_whisper(audio, "en", text)

    def test_openai_whisper_removes_generated_transcript_when_not_writing(self) -> None:
        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            command = args[0] if args else kwargs["args"]
            assert isinstance(command, list)
            output_dir = Path(command[command.index("--output_dir") + 1])
            (output_dir / "sample.txt").write_text("hello whisper\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "result.txt"
            generated = Path(tmp) / "sample.txt"

            with (
                mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper"),
                mock.patch("speed_of_cinnamon.transcriber.subprocess.run", side_effect=fake_run),
            ):
                result = transcribe_with_openai_whisper(audio, "en", text, write_transcript=False)

            self.assertEqual(result, "hello whisper")
            self.assertFalse(generated.exists())
            self.assertFalse(text.exists())

    def test_run_limited_process_rejects_empty_command(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "empty transcriber command"):
            _run_limited_process([])

    def test_run_limited_process_rejects_empty_executable(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "empty transcriber executable"):
            _run_limited_process(["  "])

    def test_run_limited_process_resolves_command_from_which(self) -> None:
        calls: list[list[str]] = []

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            command = args[0] if args else kwargs.get("args")
            assert isinstance(command, list)
            calls.append(command)
            stdout = kwargs["stdout"]
            stdout.write(b"done")
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with (
            mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper"),
            mock.patch("speed_of_cinnamon.transcriber.subprocess.run", side_effect=fake_run),
        ):
            _run_limited_process(["whisper", "audio"])

        self.assertEqual(calls[0][0], "/usr/bin/whisper")

    def test_run_limited_process_filters_dangerous_environment_variables(self) -> None:
        captured_env: dict[str, str] = {}

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            env = kwargs.get("env")
            if isinstance(env, dict):
                captured_env.update(env)
            stdout = kwargs["stdout"]
            stdout.write(b"done")
            return subprocess.CompletedProcess(["whisper"], 0, stdout=b"", stderr=b"")

        with (
            mock.patch.dict(
                "speed_of_cinnamon.transcriber.os.environ",
                {
                    "LD_PRELOAD": "malicious-lib.so",
                    "PYTHONPATH": "/tmp/evil",
                    "HOME": "/tmp/home",
                    "LANG": "en_US.UTF-8",
                    "DISPLAY": ":0",
                    "XDG_RUNTIME_DIR": "/run/user/1000",
                    "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
                },
                clear=True,
            ),
            mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper"),
            mock.patch("speed_of_cinnamon.transcriber.subprocess.run", side_effect=fake_run),
        ):
            self.assertIsNone(_run_limited_process(["whisper", "audio"]))

        self.assertNotIn("LD_PRELOAD", captured_env)
        self.assertNotIn("PYTHONPATH", captured_env)
        self.assertEqual(captured_env["DISPLAY"], ":0")
        self.assertEqual(captured_env["XDG_RUNTIME_DIR"], "/run/user/1000")
        self.assertEqual(captured_env["DBUS_SESSION_BUS_ADDRESS"], "unix:path=/run/user/1000/bus")
        self.assertEqual(captured_env["PATH"], "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")

    def test_filtered_environment_skips_non_text_environment_values(self) -> None:
        from speed_of_cinnamon.transcriber import _filtered_environment as transcriber_filtered_environment

        with mock.patch("speed_of_cinnamon.transcriber.os.environ.__getitem__", return_value=123):
            env = transcriber_filtered_environment()

        self.assertNotIn("HOME", env)
        self.assertNotIn("LANG", env)
        self.assertIn("PATH", env)

    def test_run_limited_process_rejects_non_list_command(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "transcriber command must be a list or tuple"):
            _run_limited_process("whisper", timeout=1)  # type: ignore[arg-type]

    def test_run_limited_process_accepts_tuple_command(self) -> None:
        calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            command = args[0] if args else kwargs["args"]
            stdout_file = kwargs["stdout"]
            stderr_file = kwargs["stderr"]
            if not isinstance(command, list):
                command = list(command)  # type: ignore[assignment]
            assert isinstance(command, list)
            calls.append((tuple(command), kwargs))
            stdout_file.write(b"done")
            stderr_file.write(b"")
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with (
            mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper"),
            mock.patch("speed_of_cinnamon.transcriber.subprocess.run", side_effect=fake_run),
        ):
            _run_limited_process(("whisper", "audio"))

        self.assertEqual(calls[0][0], ("/usr/bin/whisper", "audio"))

    def test_run_limited_process_rejects_missing_command(self) -> None:
        with mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value=None):
            with self.assertRaisesRegex(TranscriptionError, "is not available"):
                _run_limited_process(["whisper"])

    def test_run_limited_process_rejects_non_text_argument(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "transcriber command items must be text"):
            _run_limited_process(["whisper", 123])  # type: ignore[list-item]

    def test_run_limited_process_rejects_null_byte_in_executable(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "command argument contains invalid null byte"):
            _run_limited_process(["whisper\x00"])

    def test_run_limited_process_rejects_escaped_null_in_executable(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "command argument contains invalid null byte"):
            _run_limited_process(["whisper\\x00"])

    def test_run_limited_process_rejects_control_character_in_executable(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "command argument contains invalid control character"):
            _run_limited_process(["whisper\\n"])

    def test_run_limited_process_rejects_command_with_path_separator(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "path separators"):
            _run_limited_process(["/usr/bin/whisper"])

    def test_run_limited_process_rejects_non_positive_timeout(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "timeout must be positive"):
            _run_limited_process(["whisper"], timeout=0)

    def test_run_limited_process_rejects_arguments_with_null_byte(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "command argument contains invalid null byte"):
            _run_limited_process(["whisper", "audio\x00file"])

    def test_run_limited_process_rejects_arguments_with_escaped_null(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "command argument contains invalid null byte"):
            _run_limited_process(["whisper", "audio\\x00file"])

    def test_run_limited_process_rejects_arguments_with_control_character(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "command argument contains invalid control character"):
            _run_limited_process(["whisper", "audio\nfile"])

    def test_openai_whisper_rejects_missing_binary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            with (
                mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper"),
                mock.patch("speed_of_cinnamon.transcriber.subprocess.run", side_effect=FileNotFoundError("missing")),
            ):
                with self.assertRaisesRegex(TranscriptionError, "is not available"):
                    transcribe_with_openai_whisper(audio, "en", text)

    def test_resolve_whisper_cpp_accepts_fedora_pwcpp(self) -> None:
        def which(command: str, path: str | None = None) -> str | None:
            return "/usr/bin/pwcpp" if command == "pwcpp" else None

        with mock.patch("speed_of_cinnamon.transcriber.shutil.which", side_effect=which):
            self.assertEqual(resolve_whisper_cpp_command(), "pwcpp")

    def test_whisper_cpp_backend_supports_fedora_pwcpp_output(self) -> None:
        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            generated.write_text("hallo cinnamon\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            generated = Path(tmp) / "sample.wav.txt"
            text = Path(tmp) / "sample.txt"
            model = Path(tmp) / "ggml-base.bin"
            model.write_bytes(b"model")
            with (
                mock.patch("speed_of_cinnamon.transcriber.resolve_whisper_cpp_command", return_value="pwcpp"),
                mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/pwcpp"),
                mock.patch("speed_of_cinnamon.transcriber.subprocess.run", side_effect=fake_run) as mocked_run,
            ):
                result = transcribe_with_whisper_cpp(audio, "de", text, str(model))

            self.assertEqual(result, "hallo cinnamon")
            self.assertEqual(text.read_text(encoding="utf-8").strip(), "hallo cinnamon")
            self.assertFalse(generated.exists())
            command = mocked_run.call_args.args[0]
            self.assertEqual(command, ["/usr/bin/pwcpp", "-m", str(model), "--language", "de", "-otxt", str(audio)])

    def test_command_stdout_is_saved_as_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"not really wav but enough for command-template test")
            text = Path(tmp) / "sample.txt"
            result = transcribe(audio, "en", text, "printf 'hello cinnamon'")
            saved = text.read_text(encoding="utf-8").strip()
        self.assertEqual(result, "hello cinnamon")
        self.assertEqual(saved, "hello cinnamon")

    def test_command_receives_personalization_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            result = transcribe(
                audio,
                "en",
                text,
                "python3 -c \"import os; print(os.environ['SPEED_OF_CINNAMON_VOCABULARY'])\"",
                personal_context="Use project terms.",
                vocabulary="PipeWire\nCinnamon",
        )
        self.assertEqual(result, "PipeWire\nCinnamon")

    def test_transcribe_rejects_oversized_audio_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "huge.wav"
            audio.write_bytes(b"audio")
            with mock.patch("speed_of_cinnamon.transcriber.MAX_AUDIO_FILE_BYTES", 1):
                with self.assertRaisesRegex(TranscriptionError, "audio file is too large"):
                    transcribe(audio, "en", Path(tmp) / "sample.txt", "printf ignored")

    def test_transcribe_rejects_unsupported_audio_extension(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.txt"
            audio.write_bytes(b"audio")
            with self.assertRaisesRegex(TranscriptionError, "unsupported audio extension"):
                transcribe(audio, "en", Path(tmp) / "sample.txt", "printf ignored")

    def test_transcribe_rejects_non_path_audio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(TranscriptionError, "audio path must be a Path"):
                transcribe("sample.wav", "en", Path(tmp) / "sample.txt", "printf ok")  # type: ignore[arg-type]

    def test_transcribe_rejects_invalid_language_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            with self.assertRaisesRegex(TranscriptionError, "language must be text"):
                transcribe(audio, 42, Path(tmp) / "sample.txt", "printf ok")  # type: ignore[arg-type]
            with self.assertRaisesRegex(TranscriptionError, "language must be text"):
                transcribe(audio, True, Path(tmp) / "sample.txt", "printf ok")  # type: ignore[arg-type]

    def test_backend_aliases_are_normalized(self) -> None:
        self.assertEqual(normalize_backend("openai"), "whisper")
        self.assertEqual(normalize_backend("openai-whisper"), "whisper")
        self.assertEqual(normalize_backend("external-api"), "openai-compatible")
        self.assertEqual(normalize_backend("openai-compatible-api"), "openai-compatible")
        self.assertEqual(normalize_backend("whisper.cpp"), "whisper-cpp")
        self.assertEqual(normalize_backend("custom"), "command")
        self.assertEqual(normalize_backend("template"), "command")
        self.assertEqual(normalize_backend("faster-whisper"), "faster-whisper")
        self.assertEqual(normalize_backend(""), "auto")

    def test_transcribe_with_openai_compatible_api_posts_audio_and_writes_transcript(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self, size: int = -1) -> bytes:
                if getattr(self, "_read", False):
                    return b""
                self._read = True
                return b'{"text":"hello api"}'

        captured: dict[str, object] = {}

        def fake_open_http_request(request: object, *, timeout: int = 0, field_name: str = "") -> Response:
            captured["url"] = request.full_url
            captured["headers"] = dict(request.header_items())
            captured["data"] = request.data
            captured["timeout"] = timeout
            return Response()

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text_path = Path(tmp) / "sample.txt"
            with mock.patch("speed_of_cinnamon.transcriber._open_http_request", side_effect=fake_open_http_request):
                result = transcribe(
                    audio,
                    "de",
                    text_path,
                    backend="openai-compatible",
                    openai_compatible_model="whisper-large-v3",
                    openai_compatible_url="http://127.0.0.1:8000/v1",
                    openai_compatible_api_key="secret",
                )
            written = text_path.read_text(encoding="utf-8").strip()
        self.assertEqual(result, "hello api")
        self.assertEqual(written, "hello api")
        self.assertEqual(captured["url"], "http://127.0.0.1:8000/v1/audio/transcriptions")
        headers = captured["headers"]
        self.assertEqual(headers["Authorization"], "Bearer secret")
        self.assertIn("multipart/form-data", headers["Content-type"])
        data = captured["data"]
        self.assertIn(b'name="model"', data)
        self.assertIn(b"whisper-large-v3", data)
        self.assertIn(b'name="language"', data)
        self.assertIn(b"de", data)
        self.assertNotIn(b'name="service_tier"', data)

    def test_transcribe_with_openai_compatible_api_writes_transcript_once(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self, size: int = -1) -> bytes:
                if getattr(self, "_read", False):
                    return b""
                self._read = True
                return b'{"text":"hello api"}'

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text_path = Path(tmp) / "sample.txt"
            with (
                mock.patch("speed_of_cinnamon.transcriber._open_http_request", return_value=Response()),
                mock.patch("speed_of_cinnamon.transcriber._write_text_atomic") as mocked_write,
            ):
                result = transcribe(
                    audio,
                    "de",
                    text_path,
                    backend="openai-compatible",
                    openai_compatible_model="whisper-large-v3",
                    openai_compatible_url="http://127.0.0.1:8000/v1",
                    openai_compatible_api_key="secret",
                )

        self.assertEqual(result, "hello api")
        mocked_write.assert_called_once_with(text_path, "hello api\n")

    def test_openai_compatible_api_adds_flex_for_openai_transcription(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self, size: int = -1) -> bytes:
                if getattr(self, "_read", False):
                    return b""
                self._read = True
                return b'{"text":"hello api"}'

        captured: dict[str, object] = {}

        def fake_open_http_request(request: object, *, timeout: int = 0, field_name: str = "") -> Response:
            captured["url"] = request.full_url
            captured["data"] = request.data
            return Response()

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text_path = Path(tmp) / "sample.txt"
            with mock.patch("speed_of_cinnamon.transcriber._open_http_request", side_effect=fake_open_http_request):
                result = transcribe(
                    audio,
                    "de",
                    text_path,
                    backend="openai-compatible",
                    openai_compatible_model="gpt-4o-transcribe",
                    openai_compatible_url="https://api.openai.com/v1",
                    openai_compatible_api_key="secret",
                )
        self.assertEqual(result, "hello api")
        self.assertEqual(captured["url"], "https://api.openai.com/v1/audio/transcriptions")
        data = captured["data"]
        self.assertIn(b'name="service_tier"', data)
        self.assertIn(b"flex", data)

    def test_openai_compatible_api_can_disable_flex_for_openai_transcription(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self, size: int = -1) -> bytes:
                if getattr(self, "_read", False):
                    return b""
                self._read = True
                return b'{"text":"hello api"}'

        captured: dict[str, object] = {}

        def fake_open_http_request(request: object, *, timeout: int = 0, field_name: str = "") -> Response:
            captured["data"] = request.data
            return Response()

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text_path = Path(tmp) / "sample.txt"
            with mock.patch("speed_of_cinnamon.transcriber._open_http_request", side_effect=fake_open_http_request):
                result = transcribe(
                    audio,
                    "de",
                    text_path,
                    backend="openai-compatible",
                    openai_compatible_model="gpt-4o-transcribe",
                    openai_compatible_url="https://api.openai.com/v1",
                    openai_compatible_api_key="secret",
                    openai_compatible_flex_processing=False,
                )
        self.assertEqual(result, "hello api")
        self.assertNotIn(b'name="service_tier"', captured["data"])

    def test_openai_compatible_api_falls_back_when_transcription_flex_is_rejected(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self, size: int = -1) -> bytes:
                if getattr(self, "_read", False):
                    return b""
                self._read = True
                return b'{"text":"hello fallback"}'

        requests = []

        def fake_open_http_request(request: object, *, timeout: int = 0, field_name: str = "") -> Response:
            requests.append(request)
            if len(requests) == 1:
                raise urllib.error.HTTPError(
                    request.full_url,
                    400,
                    "Bad Request",
                    {},
                    io.BytesIO(b'{"error":{"message":"Invalid service_tier argument","type":"invalid_request_error"}}'),
                )
            return Response()

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text_path = Path(tmp) / "sample.txt"
            with mock.patch("speed_of_cinnamon.transcriber._open_http_request", side_effect=fake_open_http_request):
                result = transcribe(
                    audio,
                    "de",
                    text_path,
                    backend="openai-compatible",
                    openai_compatible_model="gpt-4o-transcribe",
                    openai_compatible_url="https://api.openai.com/v1",
                    openai_compatible_api_key="secret",
                )
        self.assertEqual(result, "hello fallback")
        self.assertIn(b'name="service_tier"', requests[0].data)
        self.assertNotIn(b'name="service_tier"', requests[1].data)

    def test_multipart_form_data_rejects_symlink_audio_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target.wav"
            target.write_bytes(b"audio")
            audio_path = Path(tmp) / "sample.wav"
            audio_path.symlink_to(target)
            with self.assertRaisesRegex(TranscriptionError, "failed to read audio file for API upload"):
                _multipart_form_data({"model": "whisper-1", "language": "en", "response_format": "json"}, "file", audio_path)

    def test_openai_compatible_api_requires_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            with self.assertRaisesRegex(TranscriptionError, "speech model is required"):
                transcribe(audio, "en", Path(tmp) / "sample.txt", backend="openai-compatible", openai_compatible_model="")

    def test_openai_compatible_api_rejects_oversized_model_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            with self.assertRaisesRegex(TranscriptionError, "OpenAI-compatible speech model is too large"):
                transcribe(
                    audio,
                    "en",
                    Path(tmp) / "sample.txt",
                    backend="openai-compatible",
                    openai_compatible_model="x" * (MAX_OPENAI_COMPATIBLE_MODEL_CHARS + 1),
                )

    def test_openai_compatible_api_rejects_oversized_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            with self.assertRaisesRegex(TranscriptionError, "OpenAI-compatible API key is too large"):
                transcribe(
                    audio,
                    "en",
                    Path(tmp) / "sample.txt",
                    backend="openai-compatible",
                    openai_compatible_model="gpt-4o-transcribe",
                    openai_compatible_api_key="x" * (MAX_OPENAI_COMPATIBLE_API_KEY_CHARS + 1),
                )

    def test_openai_compatible_api_rejects_model_with_null_byte(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            with self.assertRaisesRegex(TranscriptionError, "model contains invalid null byte"):
                transcribe(
                    audio,
                    "en",
                    Path(tmp) / "sample.txt",
                    backend="openai-compatible",
                    openai_compatible_model="gpt-4o-transcribe\x00",
                    openai_compatible_url="https://api.openai.com/v1",
                )

    def test_openai_compatible_api_rejects_model_with_newline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            with self.assertRaisesRegex(TranscriptionError, "multipart form field contains invalid control character"):
                transcribe(
                    audio,
                    "en",
                    Path(tmp) / "sample.txt",
                    backend="openai-compatible",
                    openai_compatible_model="gpt-4o-transcribe\n",
                    openai_compatible_url="https://api.openai.com/v1",
                )

    def test_openai_compatible_api_rejects_api_key_with_null_byte(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            with self.assertRaisesRegex(TranscriptionError, "API key contains invalid null byte"):
                transcribe(
                    audio,
                    "en",
                    Path(tmp) / "sample.txt",
                    backend="openai-compatible",
                    openai_compatible_model="gpt-4o-transcribe",
                    openai_compatible_url="https://api.openai.com/v1",
                    openai_compatible_api_key="secret\x00",
                )

    def test_openai_compatible_api_rejects_api_key_with_newline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            with self.assertRaisesRegex(TranscriptionError, "invalid control character"):
                transcribe(
                    audio,
                    "en",
                    Path(tmp) / "sample.txt",
                    backend="openai-compatible",
                    openai_compatible_model="gpt-4o-transcribe",
                    openai_compatible_url="https://api.openai.com/v1",
                    openai_compatible_api_key="secret\n",
                )

    def test_openai_compatible_api_rejects_url_with_newline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            with self.assertRaisesRegex(TranscriptionError, "contains invalid control character"):
                transcribe(
                    audio,
                    "en",
                    Path(tmp) / "sample.txt",
                    backend="openai-compatible",
                    openai_compatible_model="gpt-4o-transcribe",
                    openai_compatible_url="https://api.openai.com/v1\n",
                    openai_compatible_api_key="secret",
                )

    def test_openai_compatible_api_rejects_escaped_newline_in_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            with self.assertRaisesRegex(TranscriptionError, "invalid control character"):
                transcribe(
                    audio,
                    "en",
                    Path(tmp) / "sample.txt",
                    backend="openai-compatible",
                    openai_compatible_model="gpt-4o-transcribe",
                    openai_compatible_url="https://api.openai.com/v1",
                    openai_compatible_api_key="secret\\r\\n",
                )

    def test_openai_compatible_api_reports_http_error_detail_and_endpoint(self) -> None:
        class ErrorBody:
            def __init__(self) -> None:
                self._read = False
                self.closed = False

            def read(self, size: int = -1) -> bytes:
                if self._read:
                    return b""
                self._read = True
                return b'{"error":{"message":"missing API key","type":"invalid_request_error"}}'

            def close(self) -> None:
                self.closed = True

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            error = urllib.error.HTTPError(
                "https://api.openai.com/v1/audio/transcriptions",
                401,
                "Unauthorized",
                {},
                ErrorBody(),
            )
            body = error.fp
            with mock.patch("speed_of_cinnamon.transcriber._open_http_request", side_effect=error):
                with self.assertRaisesRegex(TranscriptionError, "failed \\(401\\).*\\[redacted remote error\\]") as raised:
                    transcribe(
                        audio,
                        "en",
                        Path(tmp) / "sample.txt",
                        backend="openai-compatible",
                        openai_compatible_model="gpt-4o-transcribe",
                        openai_compatible_url="https://api.openai.com/v1",
                    )
            self.assertTrue(body.closed)
            self.assertNotIn("missing API key", str(raised.exception))
            self.assertNotIn("invalid_request_error", str(raised.exception))

    def test_openai_compatible_api_redacts_json_error_payload(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self, size: int = -1) -> bytes:
                if getattr(self, "_read", False):
                    return b""
                self._read = True
                return b'{"error":{"message":"token sk-secret for Alice transcript leaked"}}'

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            with mock.patch("speed_of_cinnamon.transcriber._open_http_request", return_value=Response()):
                with self.assertRaisesRegex(TranscriptionError, "\\[redacted remote error\\]") as raised:
                    transcribe(
                        audio,
                        "en",
                        Path(tmp) / "sample.txt",
                        backend="openai-compatible",
                        openai_compatible_model="gpt-4o-transcribe",
                        openai_compatible_url="https://api.openai.com/v1",
                    )
            self.assertNotIn("sk-secret", str(raised.exception))
            self.assertNotIn("Alice", str(raised.exception))

    def test_openai_api_rejects_non_transcription_model_before_network(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            with mock.patch("speed_of_cinnamon.transcriber._open_http_request") as mocked_open_http_request:
                with self.assertRaisesRegex(TranscriptionError, "requires a speech-to-text model.*gpt-5"):
                    transcribe(
                        audio,
                        "en",
                        Path(tmp) / "sample.txt",
                        backend="openai-compatible",
                        openai_compatible_model="gpt-5",
                        openai_compatible_url="https://api.openai.com/v1",
                    )
        mocked_open_http_request.assert_not_called()

    def test_openai_compatible_api_rejects_cross_origin_redirect(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "redirect target changes origin"):
            _validate_same_origin_redirect(
                "https://api.openai.com/v1/audio/transcriptions",
                "http://127.0.0.1:8000/steal",
                field_name="OpenAI-compatible speech request",
            )

    def test_openai_compatible_api_rejects_non_http_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            with self.assertRaisesRegex(TranscriptionError, "must use http:// or https://"):
                transcribe(
                    audio,
                    "en",
                    Path(tmp) / "sample.txt",
                    backend="openai-compatible",
                    openai_compatible_model="gpt-4o-transcribe",
                    openai_compatible_url="ftp://127.0.0.1:8000/v1",
                )

    def test_openai_compatible_api_rejects_null_byte_in_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            with self.assertRaisesRegex(TranscriptionError, "contains invalid null byte"):
                transcribe(
                    audio,
                    "en",
                    Path(tmp) / "sample.txt",
                    backend="openai-compatible",
                    openai_compatible_model="gpt-4o-transcribe",
                    openai_compatible_url="http://127.0.0.1:8000/v1\x00",
                )

    def test_openai_compatible_api_rejects_filename_with_newline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "bad\nname.wav"
            audio.write_bytes(b"audio")
            with self.assertRaisesRegex(TranscriptionError, "audio path contains invalid control character"):
                transcribe(
                    audio,
                    "en",
                    Path(tmp) / "sample.txt",
                    backend="openai-compatible",
                    openai_compatible_model="gpt-4o-transcribe",
                    openai_compatible_url="https://api.openai.com/v1",
                )

    def test_non_openai_backend_ignores_invalid_openai_compatible_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text_path = Path(tmp) / "sample.txt"

            with mock.patch("speed_of_cinnamon.transcriber.transcribe_with_faster_whisper", return_value="ok transcript"):
                result = transcribe(
                    audio,
                    "en",
                    text_path,
                    backend="faster-whisper",
                    whisper_model="",
                    openai_compatible_model="gpt-4o-transcribe",
                    openai_compatible_url="ftp://127.0.0.1:8000/v1",
                )
        self.assertEqual(result, "ok transcript")

    def test_auto_prefers_custom_command(self) -> None:
        config = TranscriberConfig(command_template="printf custom")
        with mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper"):
            self.assertEqual(resolve_transcriber(config), "command")

    def test_auto_uses_whisper_command_when_installed(self) -> None:
        config = TranscriberConfig()
        with (
            mock.patch("speed_of_cinnamon.transcriber.default_whisper_cpp_model_path", return_value=""),
            mock.patch("speed_of_cinnamon.transcriber.shutil.which", side_effect=lambda name, path=None: "/usr/bin/whisper" if name == "whisper" else None),
        ):
            self.assertEqual(resolve_transcriber(config), "whisper")

    def test_auto_uses_whisper_cpp_when_model_is_configured(self) -> None:
        def which(command: str, path: str | None = None) -> str | None:
            return "/usr/bin/whisper-cli" if command == "whisper-cli" else None

        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "ggml-base.bin"
            model.write_bytes(b"model")
            config = TranscriberConfig(whisper_model=str(model))
            with (
                mock.patch("speed_of_cinnamon.transcriber.default_whisper_cpp_model_path", return_value=""),
                mock.patch("speed_of_cinnamon.transcriber.shutil.which", side_effect=which),
            ):
                self.assertEqual(resolve_transcriber(config), "whisper-cpp")

    def test_auto_uses_downloaded_whisper_cpp_model(self) -> None:
        def which(command: str, path: str | None = None) -> str | None:
            return "/usr/bin/whisper-cli" if command == "whisper-cli" else None

        with (
            mock.patch("speed_of_cinnamon.transcriber.default_ctranslate2_model_path", return_value=""),
            mock.patch("speed_of_cinnamon.transcriber.default_whisper_cpp_model_path", return_value="/models/ggml-tiny.en.bin"),
            mock.patch("speed_of_cinnamon.transcriber.shutil.which", side_effect=which),
        ):
            self.assertEqual(resolve_transcriber(TranscriberConfig()), "whisper-cpp")

    def test_whisper_cpp_rejects_english_only_model_for_non_english_language(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            text = Path(tmp) / "sample.txt"
            audio.write_bytes(b"audio")
            model = Path(tmp) / "ggml-tiny.en.bin"
            model.write_bytes(b"model")

            with self.assertRaisesRegex(TranscriptionError, "English-only whisper.cpp model"):
                transcribe_with_whisper_cpp(audio, "de", text, str(model))

    def test_auto_reports_missing_transcriber(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.transcriber.default_ctranslate2_model_path", return_value=""),
            mock.patch("speed_of_cinnamon.transcriber.default_whisper_cpp_model_path", return_value=""),
            mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value=None),
        ):
            with self.assertRaisesRegex(TranscriptionError, "no transcriber available"):
                resolve_transcriber(TranscriberConfig())

    def test_configured_missing_model_does_not_fall_back_to_whisper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "missing.bin"
            with mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper"):
                with self.assertRaisesRegex(TranscriptionError, "path is missing"):
                    resolve_transcriber(TranscriberConfig(whisper_model=str(model)))

    def test_configured_faster_whisper_model_requires_existing_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "ct2" / "config.json"
            with (
                mock.patch("speed_of_cinnamon.transcriber.model_backend_for_path", return_value="faster-whisper"),
                mock.patch("speed_of_cinnamon.transcriber.resolve_whisper_cpp_command", return_value=""),
                mock.patch("speed_of_cinnamon.transcriber.faster_whisper_available", return_value=False),
            ):
                with self.assertRaisesRegex(TranscriptionError, "path is missing"):
                    resolve_transcriber(TranscriberConfig(whisper_model=str(model)))

    def test_auto_infers_faster_whisper_for_configured_directory_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "custom-ct2-model"
            model.mkdir()
            with (
                mock.patch("speed_of_cinnamon.transcriber.model_backend_for_path", return_value=""),
                mock.patch("speed_of_cinnamon.transcriber.faster_whisper_available", return_value=True),
                mock.patch("speed_of_cinnamon.transcriber.resolve_whisper_cpp_command", return_value="/usr/bin/whisper-cli"),
            ):
                self.assertEqual(resolve_transcriber(TranscriberConfig(whisper_model=str(model))), "faster-whisper")

    def test_configured_directory_model_requires_faster_whisper_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "custom-ct2-model"
            model.mkdir()
            with (
                mock.patch("speed_of_cinnamon.transcriber.model_backend_for_path", return_value=""),
                mock.patch("speed_of_cinnamon.transcriber.faster_whisper_available", return_value=False),
                mock.patch("speed_of_cinnamon.transcriber.resolve_whisper_cpp_command", return_value="/usr/bin/whisper-cli"),
            ):
                with self.assertRaisesRegex(TranscriptionError, "requires faster-whisper"):
                    resolve_transcriber(TranscriberConfig(whisper_model=str(model)))

    def test_configured_missing_directory_model_reports_missing_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "missing-ct2-model"
            with (
                mock.patch("speed_of_cinnamon.transcriber.model_backend_for_path", return_value=""),
                mock.patch("speed_of_cinnamon.transcriber.faster_whisper_available", return_value=True),
            ):
                with self.assertRaisesRegex(TranscriptionError, "path is missing"):
                    resolve_transcriber(TranscriberConfig(whisper_model=str(model)))

    def test_configured_custom_model_requires_whisper_cpp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "custom.bin"
            model.write_bytes(b"model")
            with (
                mock.patch("speed_of_cinnamon.transcriber.resolve_whisper_cpp_command", return_value=""),
                mock.patch("speed_of_cinnamon.transcriber.faster_whisper_available", return_value=False),
            ):
                with self.assertRaisesRegex(TranscriptionError, "requires whisper.cpp"):
                    resolve_transcriber(TranscriberConfig(whisper_model=str(model)))

    def test_resolve_transcriber_rejects_null_byte_model(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "whisper model contains invalid null byte"):
            resolve_transcriber(TranscriberConfig(backend="auto", whisper_model="x\x00"))

    def test_resolve_transcriber_rejects_escaped_null_model(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "whisper model contains invalid null byte"):
            resolve_transcriber(TranscriberConfig(backend="auto", whisper_model="x\\x00y"))

    def test_explicit_command_backend_requires_template(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            with self.assertRaisesRegex(TranscriptionError, "custom transcriber command is required"):
                transcribe(audio, "en", Path(tmp) / "sample.txt", backend="command")

    def test_explicit_whisper_cpp_backend_requires_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            with (
                mock.patch("speed_of_cinnamon.transcriber.default_whisper_cpp_model_path", return_value=""),
                mock.patch("speed_of_cinnamon.transcriber.resolve_whisper_cpp_command", return_value="whisper-cli"),
                self.assertRaisesRegex(TranscriptionError, "model path is required"),
            ):
                transcribe(audio, "en", Path(tmp) / "sample.txt", backend="whisper-cpp")


if __name__ == "__main__":
    unittest.main()
