from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from speed_of_cinnamon.transcriber import (
    TranscriberConfig,
    TranscriptionError,
    MAX_AUDIO_FILE_BYTES,
    _read_file_head,
    _read_text_file,
    _assert_text_length,
    _contains_escaped_null,
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

    def test_validate_audio_file_rejects_null_byte_path(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "invalid null byte"):
            validate_audio_file(Path("sample\x00.wav"))

    def test_validate_audio_file_rejects_escaped_null_path(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "invalid null byte"):
            validate_audio_file(Path("sample\\x00.wav"))

    def test_validate_audio_file_rejects_non_path(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "audio path must be a Path"):
            validate_audio_file("sample.wav")  # type: ignore[arg-type]

    def test_assert_text_length_rejects_non_text(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "must be text"):
            _assert_text_length(12, field_name="text")

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
            with self.assertRaisesRegex(TranscriptionError, "command not found"):
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

    def test_run_limited_process_rejects_non_list_command(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "transcriber command must be a list"):
            _run_limited_process("whisper", timeout=1)  # type: ignore[arg-type]

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

    def test_run_limited_process_rejects_non_positive_timeout(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "timeout must be positive"):
            _run_limited_process(["whisper"], timeout=0)

    def test_run_limited_process_rejects_arguments_with_null_byte(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "command argument contains invalid null byte"):
            _run_limited_process(["whisper", "audio\x00file"])

    def test_run_limited_process_rejects_arguments_with_escaped_null(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "command argument contains invalid null byte"):
            _run_limited_process(["whisper", "audio\\x00file"])

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
        def which(command: str) -> str | None:
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
        self.assertEqual(normalize_backend("openai-whisper"), "whisper")
        self.assertEqual(normalize_backend("whisper.cpp"), "whisper-cpp")
        self.assertEqual(normalize_backend("custom"), "command")
        self.assertEqual(normalize_backend(""), "auto")

    def test_auto_prefers_custom_command(self) -> None:
        config = TranscriberConfig(command_template="printf custom")
        with mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper"):
            self.assertEqual(resolve_transcriber(config), "command")

    def test_auto_uses_whisper_command_when_installed(self) -> None:
        config = TranscriberConfig()
        with (
            mock.patch("speed_of_cinnamon.transcriber.default_whisper_cpp_model_path", return_value=""),
            mock.patch("speed_of_cinnamon.transcriber.shutil.which", side_effect=lambda name: "/usr/bin/whisper" if name == "whisper" else None),
        ):
            self.assertEqual(resolve_transcriber(config), "whisper")

    def test_auto_uses_whisper_cpp_when_model_is_configured(self) -> None:
        def which(command: str) -> str | None:
            return "/usr/bin/whisper-cli" if command == "whisper-cli" else None

        config = TranscriberConfig(whisper_model="/models/ggml-base.bin")
        with (
            mock.patch("speed_of_cinnamon.transcriber.default_whisper_cpp_model_path", return_value=""),
            mock.patch("speed_of_cinnamon.transcriber.shutil.which", side_effect=which),
        ):
            self.assertEqual(resolve_transcriber(config), "whisper-cpp")

    def test_auto_uses_downloaded_whisper_cpp_model(self) -> None:
        def which(command: str) -> str | None:
            return "/usr/bin/whisper-cli" if command == "whisper-cli" else None

        with (
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
            mock.patch("speed_of_cinnamon.transcriber.default_whisper_cpp_model_path", return_value=""),
            mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value=None),
        ):
            with self.assertRaisesRegex(TranscriptionError, "no transcriber available"):
                resolve_transcriber(TranscriberConfig())

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
