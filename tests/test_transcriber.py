# mypy: ignore-errors
from __future__ import annotations

import os
import io
import sys
import stat as stat_module
import subprocess
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from speed_of_cinnamon import transcriber as transcriber_module
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
    _read_response_text,
    _read_text_file,
    _validate_audio_path_shape,
    _assert_text_length,
    _contains_escaped_null,
    _contains_http_header_control_chars,
    _contains_multipart_control_chars,
    _validate_same_origin_redirect,
    _open_http_request,
    _quote,
    _remove_generated_transcript_file,
    _write_text_atomic,
    validate_audio_file,
    _run_limited_process,
    transcribe_with_openai_compatible_api,
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
    def test_openai_compatible_http_opener_disables_environment_proxies(self) -> None:
        request = urllib.request.Request("https://example.test/v1/audio/transcriptions")
        opener = mock.Mock()
        sentinel = object()
        opener.open.return_value = sentinel
        with mock.patch("speed_of_cinnamon.transcriber.urllib.request.build_opener", return_value=opener) as build_opener:
            self.assertIs(_open_http_request(request, timeout=7, field_name="speech request"), sentinel)

        handlers = build_opener.call_args.args
        self.assertTrue(any(isinstance(handler, urllib.request.ProxyHandler) for handler in handlers))
        self.assertTrue(any(getattr(handler, "proxies", None) == {} for handler in handlers))
        opener.open.assert_called_once_with(request, timeout=7)

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

    def test_template_does_not_expand_placeholders_inside_inserted_values(self) -> None:
        rendered = render_command_template(
            "tool --audio {audio} --prompt {prompt}",
            Path("/tmp/sample{prompt}.wav"),
            "de",
            Path("/tmp/out.txt"),
            "private context",
            "",
        )

        self.assertIn("sample{prompt}.wav", rendered)
        self.assertEqual(rendered.count("private context"), 1)

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

    def test_transcribe_does_not_persist_raw_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            result = transcribe(
                audio,
                "en",
                text,
                "printf hello",
            )
            self.assertEqual(result, "hello")
            self.assertFalse(text.exists())

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

    def test_transcribe_prepares_transcript_directory_without_following_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "nested" / "sample.txt"
            with mock.patch(
                "speed_of_cinnamon.transcriber.ensure_directory_without_following_symlinks",
                wraps=transcriber_module.ensure_directory_without_following_symlinks,
            ) as mocked_ensure:
                result = transcribe(
                    audio,
                    "en",
                    text,
                    "printf hello",
                )

            self.assertEqual(result, "hello")
            mocked_ensure.assert_any_call(text.parent, field_name="transcript directory")
            self.assertTrue(text.parent.is_dir())
            self.assertFalse(text.exists())

    def test_write_text_atomic_sets_private_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "transcript.txt"
            _write_text_atomic(path, "private output")
            mode = path.stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)

    def test_write_text_atomic_removes_temp_file_when_write_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "transcript.txt"
            with mock.patch(
                "speed_of_cinnamon.transcriber.write_text_atomically_without_following_symlinks",
                side_effect=OSError(f"disk full {target}"),
            ):
                with self.assertRaisesRegex(TranscriptionError, "failed to write transcript file") as raised:
                    _write_text_atomic(target, "private output")

            self.assertFalse(target.exists())
            self.assertNotIn(str(target), str(raised.exception))

    def test_remove_generated_transcript_file_removes_regular_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            generated = Path(tmp) / "generated.txt"
            generated.write_text("temporary", encoding="utf-8")

            _remove_generated_transcript_file(generated, field_name="generated transcript")

            self.assertFalse(generated.exists())

    def test_remove_generated_transcript_file_fsyncs_parent_after_delete(self) -> None:
        fsync_modes: list[int] = []
        real_fsync = os.fsync

        def record_fsync(fd: int) -> None:
            fsync_modes.append(os.fstat(fd).st_mode)
            real_fsync(fd)

        with tempfile.TemporaryDirectory() as tmp:
            generated = Path(tmp) / "generated.txt"
            generated.write_text("temporary", encoding="utf-8")

            with mock.patch("speed_of_cinnamon.transcriber.os.fsync", side_effect=record_fsync):
                _remove_generated_transcript_file(generated, field_name="generated transcript")

            self.assertFalse(generated.exists())

        self.assertTrue(any(stat_module.S_ISDIR(mode) for mode in fsync_modes))

    def test_remove_generated_transcript_file_rejects_path_swap_before_delete(self) -> None:
        real_stat = os.stat
        swapped = False

        def stat_with_swap(path: object, *args: object, **kwargs: object) -> os.stat_result:
            nonlocal swapped
            result = real_stat(path, *args, **kwargs)
            if path == "generated.txt" and kwargs.get("dir_fd") is not None and not swapped:
                generated.unlink()
                generated.write_text("attacker", encoding="utf-8")
                swapped = True
                return real_stat(path, *args, **kwargs)
            return result

        with tempfile.TemporaryDirectory() as tmp:
            generated = Path(tmp) / "generated.txt"
            generated.write_text("temporary", encoding="utf-8")

            with mock.patch("speed_of_cinnamon.transcriber.os.stat", side_effect=stat_with_swap):
                with self.assertRaisesRegex(TranscriptionError, "changed before removal"):
                    _remove_generated_transcript_file(generated, field_name="generated transcript")

            self.assertTrue(generated.exists())
            self.assertEqual(generated.read_text(encoding="utf-8"), "attacker")

    def test_remove_generated_transcript_file_rejects_hardlinked_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            seed = Path(tmp) / "seed.txt"
            generated = Path(tmp) / "generated.txt"
            seed.write_text("seed content", encoding="utf-8")
            os.link(seed, generated)

            with self.assertRaisesRegex(TranscriptionError, "must not be hardlinked"):
                _remove_generated_transcript_file(generated, field_name="generated transcript")

            self.assertTrue(seed.exists())
            self.assertTrue(generated.exists())

    def test_remove_generated_transcript_file_rejects_symlink_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            external = Path(tmp) / "external.txt"
            generated = Path(tmp) / "generated.txt"
            external.write_text("outside", encoding="utf-8")
            generated.symlink_to(external)

            with self.assertRaisesRegex(TranscriptionError, "failed to open generated transcript for safe removal"):
                _remove_generated_transcript_file(generated, field_name="generated transcript")

            self.assertTrue(external.exists())
            self.assertEqual(external.read_text(encoding="utf-8"), "outside")

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

    def test_template_with_text_placeholder_preserves_existing_text_path_on_command_error(self) -> None:
        def command_fails(*_args: object, **_kwargs: object) -> str:
            text.write_text("command-output", encoding="utf-8")
            raise CommandChainError("command failed")

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            text.write_text("old", encoding="utf-8")
            with (
                mock.patch("speed_of_cinnamon.transcriber.split_command_chain", return_value=[("cmd",)]),
                mock.patch("speed_of_cinnamon.transcriber.run_command_chain", side_effect=command_fails),
            ):
                with self.assertRaisesRegex(TranscriptionError, "command failed"):
                    transcribe_with_template("{text}", audio, "en", text)

            self.assertEqual(text.read_text(encoding="utf-8"), "old")

    def test_template_with_command_error_redacts_output_and_stderr(self) -> None:
        def command_fails(*_args: object, **_kwargs: object) -> str:
            text.write_text("command-output", encoding="utf-8")
            raise CommandChainError(
                'transcriber command failed: exit code 127; stdout: secret transcript\n'
                "stderr: /tmp/secret/api-key=sk-leak token=abc"
            )

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            text.write_text("old", encoding="utf-8")
            with (
                mock.patch("speed_of_cinnamon.transcriber.split_command_chain", return_value=[("cmd",)]),
                mock.patch("speed_of_cinnamon.transcriber.run_command_chain", side_effect=command_fails),
            ):
                with self.assertRaisesRegex(TranscriptionError, "transcriber command failed: exit code 127; command output redacted") as raised:
                    transcribe_with_template("{text}", audio, "en", text)

            message = str(raised.exception)
            self.assertIn("transcriber command failed", message)
            self.assertNotIn("secret transcript", message)
            self.assertNotIn("stderr", message)
            self.assertNotIn("sk-leak", message)
            self.assertNotIn("api-key", message)
            self.assertEqual(text.read_text(encoding="utf-8"), "old")

    def test_template_with_text_placeholder_preserves_existing_text_path_on_read_error(self) -> None:
        def command_writes_invalid(*_args: object, **_kwargs: object) -> str:
            text.write_bytes(b"invalid\\x00text")
            return "generated transcript"

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            text.write_text("old", encoding="utf-8")
            with (
                mock.patch("speed_of_cinnamon.transcriber.split_command_chain", return_value=[("cmd",)]),
                mock.patch("speed_of_cinnamon.transcriber.run_command_chain", side_effect=command_writes_invalid),
            ):
                with self.assertRaisesRegex(TranscriptionError, "failed to read generated transcript"):
                    transcribe_with_template("{text}", audio, "en", text)

            self.assertEqual(text.read_text(encoding="utf-8"), "old")

    def test_template_with_text_placeholder_preserves_existing_text_path_on_validation_error(self) -> None:
        def command_writes_long_transcript(*_args: object, **_kwargs: object) -> str:
            text.write_text("too long transcript", encoding="utf-8")
            return "generated transcript"

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            text.write_text("old", encoding="utf-8")
            with (
                mock.patch("speed_of_cinnamon.transcriber.split_command_chain", return_value=[("cmd",)]),
                mock.patch("speed_of_cinnamon.transcriber.MAX_TRANSCRIPT_TEXT_CHARS", 4),
                mock.patch("speed_of_cinnamon.transcriber.run_command_chain", side_effect=command_writes_long_transcript),
            ):
                with self.assertRaisesRegex(TranscriptionError, "transcript file text is too large"):
                    transcribe_with_template("{text}", audio, "en", text)

            self.assertEqual(text.read_text(encoding="utf-8"), "old")

    def test_template_with_text_placeholder_removes_new_text_path_on_read_error(self) -> None:
        def command_writes_invalid(*_args: object, **_kwargs: object) -> str:
            text.write_bytes(b"invalid\\x00text")
            return "generated transcript"

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            with (
                mock.patch("speed_of_cinnamon.transcriber.split_command_chain", return_value=[("cmd",)]),
                mock.patch("speed_of_cinnamon.transcriber.run_command_chain", side_effect=command_writes_invalid),
            ):
                with self.assertRaisesRegex(TranscriptionError, "failed to read generated transcript"):
                    transcribe_with_template("{text}", audio, "en", text)

            self.assertFalse(text.exists())

    def test_template_with_text_placeholder_reports_cleanup_error_on_read_error(self) -> None:
        def command_writes_invalid(*_args: object, **_kwargs: object) -> str:
            text.write_bytes(b"invalid\\x00text")
            return "generated transcript"

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"

            with (
                mock.patch("speed_of_cinnamon.transcriber.split_command_chain", return_value=[("cmd",)]),
                mock.patch(
                    "speed_of_cinnamon.transcriber.run_command_chain",
                    side_effect=command_writes_invalid,
                ),
                mock.patch(
                    "speed_of_cinnamon.transcriber._remove_generated_transcript_file",
                    side_effect=TranscriptionError("failed to remove generated transcript"),
                ),
            ):
                with self.assertRaisesRegex(TranscriptionError, "failed to read generated transcript") as raised:
                    transcribe_with_template("{text}", audio, "en", text)

        self.assertTrue(
            any(
                "transcript cleanup failed: failed to remove generated transcript" in note
                for note in getattr(raised.exception, "__notes__", [])
            )
        )

    def test_template_with_text_placeholder_reports_cleanup_error_on_command_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"

            with (
                mock.patch("speed_of_cinnamon.transcriber.split_command_chain", return_value=[("cmd",)]),
                mock.patch(
                    "speed_of_cinnamon.transcriber.run_command_chain",
                    side_effect=CommandChainError("command failed"),
                ),
                mock.patch(
                    "speed_of_cinnamon.transcriber._remove_generated_transcript_file",
                    side_effect=TranscriptionError("failed to remove generated transcript"),
                ),
            ):
                with self.assertRaisesRegex(TranscriptionError, "command failed") as raised:
                    transcribe_with_template("{text}", audio, "en", text)

            self.assertTrue(
                any(
                    "transcript cleanup failed: failed to remove generated transcript" in note
                    for note in getattr(raised.exception, "__notes__", [])
                )
            )

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

    def test_read_text_file_rejects_oversized_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.txt"
            path.write_text("abcde", encoding="utf-8")

            with mock.patch("speed_of_cinnamon.transcriber.MAX_TRANSCRIPT_TEXT_CHARS", 4):
                with self.assertRaisesRegex(TranscriptionError, "failed to read generated transcript") as raised:
                    _read_text_file(path)
            self.assertNotIn(str(path), str(raised.exception))

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

    def test_backend_helpers_reject_symlink_audio_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "real.wav"
            target.write_bytes(b"audio")
            link = Path(tmp) / "sample.wav"
            link.symlink_to(target)
            text_path = Path(tmp) / "sample.txt"
            model = Path(tmp) / "ggml-base.bin"
            model.write_bytes(b"model")

            cases = [
                lambda: transcribe_with_openai_whisper(link, "en", text_path),
                lambda: transcribe_with_whisper_cpp(link, "en", text_path, str(model)),
                lambda: transcriber_module.transcribe_with_faster_whisper(link, "en", text_path, str(model)),
            ]
            for call in cases:
                with self.subTest(call=call):
                    with self.assertRaisesRegex(TranscriptionError, "audio path must not pass through a symlink"):
                        call()

    def test_snapshot_existing_file_rejects_hardlinked_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.txt"
            path.write_text("existing transcript", encoding="utf-8")
            hardlink = Path(tmp) / "sample-hardlink.txt"
            try:
                os.link(path, hardlink)
            except OSError as exc:
                self.skipTest(f"hardlinks unavailable: {exc}")

            with self.assertRaisesRegex(TranscriptionError, "failed to snapshot existing transcript file") as raised:
                transcriber_module._snapshot_existing_file(hardlink)
            self.assertNotIn(str(hardlink), str(raised.exception))

    def test_snapshot_existing_file_rejects_fifo_without_blocking(self) -> None:
        if not hasattr(os, "mkfifo"):
            self.skipTest("mkfifo unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            fifo = Path(tmp) / "sample.txt"
            os.mkfifo(fifo)

            with self.assertRaisesRegex(TranscriptionError, "failed to snapshot existing transcript file") as raised:
                transcriber_module._snapshot_existing_file(fifo)
            self.assertNotIn(str(fifo), str(raised.exception))

    def test_restore_existing_file_error_does_not_leak_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "secret-transcript.txt"
            with mock.patch(
                "speed_of_cinnamon.transcriber.write_bytes_atomically_without_following_symlinks",
                side_effect=OSError(f"restore failed {path}"),
            ):
                with self.assertRaisesRegex(TranscriptionError, "failed to restore existing transcript file") as raised:
                    transcriber_module._restore_existing_file_snapshot(path, b"previous transcript")
            self.assertNotIn(str(path), str(raised.exception))

    def test_transcribe_prepare_directory_error_does_not_leak_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text_path = Path(tmp) / "private" / "secret-transcript.txt"
            with mock.patch(
                "speed_of_cinnamon.transcriber.ensure_directory_without_following_symlinks",
                side_effect=OSError(f"cannot prepare {text_path.parent}"),
            ):
                with self.assertRaisesRegex(TranscriptionError, "failed to prepare transcript directory") as raised:
                    transcriber_module.transcribe(audio, "en", text_path, backend="command", command_template="printf hello")
            self.assertNotIn(str(text_path.parent), str(raised.exception))

    def test_read_private_file_bytes_rejects_hardlinked_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.wav"
            path.write_bytes(b"audio")
            hardlink = Path(tmp) / "sample-hardlink.wav"
            try:
                os.link(path, hardlink)
            except OSError as exc:
                self.skipTest(f"hardlinks unavailable: {exc}")

            with self.assertRaisesRegex(TranscriptionError, "failed to read audio file for API upload"):
                transcriber_module._read_private_file_bytes(hardlink, field_name="audio file for API upload")

    def test_read_private_file_bytes_rejects_fifo_without_blocking(self) -> None:
        if not hasattr(os, "mkfifo"):
            self.skipTest("mkfifo unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            fifo = Path(tmp) / "sample.wav"
            os.mkfifo(fifo)

            with self.assertRaisesRegex(TranscriptionError, "failed to read audio file for API upload"):
                transcriber_module._read_private_file_bytes(fifo, field_name="audio file for API upload")

    def test_read_private_file_bytes_rejects_oversized_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.wav"
            path.write_bytes(b"abcde")

            with self.assertRaisesRegex(TranscriptionError, "audio file for API upload is too large"):
                transcriber_module._read_private_file_bytes(
                    path,
                    field_name="audio file for API upload",
                    max_bytes=4,
                )

    @mock.patch("speed_of_cinnamon.transcriber.os.open", wraps=transcriber_module.os.open)
    def test_read_private_file_bytes_opens_file_relative_to_parent_fd(self, mocked_open: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.wav"
            path.write_bytes(b"audio")
            data = transcriber_module._read_private_file_bytes(path, field_name="audio file for API upload")
        self.assertEqual(data, b"audio")
        self.assertTrue(
            any(
                isinstance(args[0], str)
                and args[0] == path.name
                and "dir_fd" in kwargs
                and isinstance(kwargs.get("dir_fd"), int)
                and (args[1] & os.O_NOFOLLOW if len(args) > 1 else 0)
                for args, kwargs in mocked_open.call_args_list
            )
        )

    def test_read_private_file_bytes_default_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.wav"
            path.write_bytes(b"12345")
            path.chmod(0o600)
            with mock.patch("speed_of_cinnamon.transcriber.MAX_AUDIO_FILE_BYTES", 4):
                with self.assertRaisesRegex(TranscriptionError, "audio file for API upload is too large"):
                    transcriber_module._read_private_file_bytes(path, field_name="audio file for API upload")

    def test_validate_audio_file_rejects_oversized_path_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ("😀" * ((MAX_AUDIO_PATH_CHARS // 4) + 1))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"audio")
            with self.assertRaisesRegex(TranscriptionError, "audio file path is too long"):
                validate_audio_file(path)

    def test_validate_audio_path_shape_rejects_malformed_utf8(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "invalid UTF-8"):
            _validate_audio_path_shape(Path("\ud800.wav"))

    def test_assert_text_length_rejects_non_text(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "must be text"):
            _assert_text_length(12, field_name="text")

    def test_assert_text_length_rejects_oversized_text_bytes(self) -> None:
        with mock.patch("speed_of_cinnamon.transcriber.MAX_TRANSCRIPT_TEXT_CHARS", 4):
            with self.assertRaisesRegex(TranscriptionError, "is too large"):
                _assert_text_length("😀😀", field_name="transcript")

    def test_assert_text_length_rejects_malformed_utf8(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "invalid UTF-8"):
            _assert_text_length("\ud800", field_name="transcript")

    def test_assert_text_length_rejects_null_byte(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "transcript contains invalid null byte"):
            _assert_text_length("hello\x00secret", field_name="transcript")

    def test_read_response_text_rejects_invalid_max_bytes(self) -> None:
        response = mock.Mock()
        response.read.return_value = b""
        with self.assertRaisesRegex(TranscriptionError, "max response bytes must be an integer"):
            _read_response_text(response, True)  # type: ignore[arg-type]

    def test_read_response_text_rejects_negative_max_bytes(self) -> None:
        response = mock.Mock()
        response.read.return_value = b""
        with self.assertRaisesRegex(TranscriptionError, "max response bytes must be non-negative"):
            _read_response_text(response, -1)

    def test_read_response_text_rejects_non_bytes_chunks(self) -> None:
        response = mock.Mock()
        response.read.side_effect = ["not-bytes", b""]
        with self.assertRaisesRegex(TranscriptionError, "API response chunk must be bytes"):
            _read_response_text(response)

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

    def test_normalize_backend_rejects_control_character(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "backend contains invalid control character"):
            normalize_backend("\x85openai-compatible")

    def test_normalize_backend_rejects_escaped_control_character(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "backend contains invalid control character"):
            normalize_backend("\\x85openai-compatible")

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
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            generated = Path(tmp) / "sample.txt"
            generated.write_text("hello", encoding="utf-8")

            with (
                mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper"),
                mock.patch("speed_of_cinnamon.transcriber.MAX_COMMAND_OUTPUT_CHARS", 4),
                mock.patch(
                    "speed_of_cinnamon.transcriber._run_transcriber_process",
                    side_effect=CommandChainError("transcriber command output exceeded 4 bytes"),
                ),
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
                mock.patch("speed_of_cinnamon.transcriber._run_transcriber_process", side_effect=fake_run),
            ):
                result = transcribe_with_openai_whisper(audio, "en", text, write_transcript=False)

            self.assertEqual(result, "hello whisper")
            self.assertFalse(generated.exists())
            self.assertFalse(text.exists())

    def test_openai_whisper_removes_matching_text_path_when_not_writing(self) -> None:
        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            command = args[0] if args else kwargs["args"]
            assert isinstance(command, list)
            output_dir = Path(command[command.index("--output_dir") + 1])
            (output_dir / "sample.txt").write_text("hello whisper\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"

            with (
                mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper"),
                mock.patch("speed_of_cinnamon.transcriber._run_transcriber_process", side_effect=fake_run),
            ):
                result = transcribe_with_openai_whisper(audio, "en", text, write_transcript=False)

            self.assertEqual(result, "hello whisper")
            self.assertFalse(text.exists())

    def test_openai_whisper_fails_when_cleanup_fails_with_write_transcript_false(self) -> None:
        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            command = args[0] if args else kwargs["args"]
            assert isinstance(command, list)
            output_dir = Path(command[command.index("--output_dir") + 1])
            (output_dir / "sample.txt").write_text("hello whisper\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"

            with (
                mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper"),
                mock.patch("speed_of_cinnamon.transcriber._run_transcriber_process", side_effect=fake_run),
                mock.patch(
                    "speed_of_cinnamon.transcriber._remove_generated_transcript_file",
                    side_effect=TranscriptionError("failed to remove generated transcript"),
                ),
            ):
                with self.assertRaisesRegex(
                    TranscriptionError,
                    "failed to remove generated transcript",
                ):
                    transcribe_with_openai_whisper(audio, "en", text, write_transcript=False)

    def test_openai_whisper_keeps_existing_matching_text_path_when_not_writing(self) -> None:
        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            command = args[0] if args else kwargs["args"]
            assert isinstance(command, list)
            output_dir = Path(command[command.index("--output_dir") + 1])
            (output_dir / "sample.txt").write_text("hello whisper\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            text.write_text("existing transcript", encoding="utf-8")

            with (
                mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper"),
                mock.patch("speed_of_cinnamon.transcriber._run_transcriber_process", side_effect=fake_run),
            ):
                result = transcribe_with_openai_whisper(audio, "en", text, write_transcript=False)

            self.assertEqual(result, "hello whisper")
            self.assertEqual(text.read_text(encoding="utf-8"), "existing transcript")

    def test_openai_whisper_preserves_existing_matching_text_path_on_error_when_not_writing(self) -> None:
        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            command = args[0] if args else kwargs["args"]
            assert isinstance(command, list)
            output_dir = Path(command[command.index("--output_dir") + 1])
            (output_dir / "sample.txt").write_bytes(b"invalid\x00text")
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            text.write_text("existing transcript", encoding="utf-8")

            with (
                mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper"),
                mock.patch("speed_of_cinnamon.transcriber._run_transcriber_process", side_effect=fake_run),
            ):
                with self.assertRaisesRegex(TranscriptionError, "failed to read generated transcript"):
                    transcribe_with_openai_whisper(audio, "en", text, write_transcript=False)

            self.assertEqual(text.read_text(encoding="utf-8"), "existing transcript")

    def test_openai_whisper_preserves_existing_matching_text_path_on_read_error_when_writing(self) -> None:
        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            command = args[0] if args else kwargs["args"]
            assert isinstance(command, list)
            output_dir = Path(command[command.index("--output_dir") + 1])
            (output_dir / "sample.txt").write_bytes(b"invalid\x00text")
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            text.write_text("existing transcript", encoding="utf-8")

            with (
                mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper"),
                mock.patch("speed_of_cinnamon.transcriber._run_transcriber_process", side_effect=fake_run),
            ):
                with self.assertRaisesRegex(TranscriptionError, "failed to read generated transcript"):
                    transcribe_with_openai_whisper(audio, "en", text)

            self.assertEqual(text.read_text(encoding="utf-8"), "existing transcript")

    def test_openai_whisper_preserves_existing_matching_text_path_on_validation_error_when_writing(self) -> None:
        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            command = args[0] if args else kwargs["args"]
            assert isinstance(command, list)
            output_dir = Path(command[command.index("--output_dir") + 1])
            (output_dir / "sample.txt").write_text("hello whisper\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            text.write_text("old", encoding="utf-8")

            with (
                mock.patch("speed_of_cinnamon.transcriber.MAX_TRANSCRIPT_TEXT_CHARS", 4),
                mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper"),
                mock.patch("speed_of_cinnamon.transcriber._run_transcriber_process", side_effect=fake_run),
            ):
                with self.assertRaisesRegex(TranscriptionError, "transcript is too large"):
                    transcribe_with_openai_whisper(audio, "en", text)

            self.assertEqual(text.read_text(encoding="utf-8"), "old")

    def test_openai_whisper_removes_generated_transcript_after_writing(self) -> None:
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
                mock.patch("speed_of_cinnamon.transcriber._run_transcriber_process", side_effect=fake_run),
            ):
                result = transcribe_with_openai_whisper(audio, "en", text)

            self.assertEqual(result, "hello whisper")
            self.assertFalse(generated.exists())
            self.assertEqual(text.read_text(encoding="utf-8"), "hello whisper\n")

    def test_openai_whisper_keeps_transcript_when_generated_path_matches_text_path(self) -> None:
        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            command = args[0] if args else kwargs["args"]
            assert isinstance(command, list)
            output_dir = Path(command[command.index("--output_dir") + 1])
            (output_dir / "sample.txt").write_text("hello whisper\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"

            with (
                mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper"),
                mock.patch("speed_of_cinnamon.transcriber._run_transcriber_process", side_effect=fake_run),
            ):
                result = transcribe_with_openai_whisper(audio, "en", text)

            self.assertEqual(result, "hello whisper")
            self.assertEqual(text.read_text(encoding="utf-8"), "hello whisper\n")

    def test_openai_whisper_restores_existing_generated_sidecar_after_writing(self) -> None:
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
            generated.write_text("existing transcript\n", encoding="utf-8")

            with (
                mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper"),
                mock.patch("speed_of_cinnamon.transcriber._run_transcriber_process", side_effect=fake_run),
            ):
                result = transcribe_with_openai_whisper(audio, "en", text)

            self.assertEqual(result, "hello whisper")
            self.assertEqual(text.read_text(encoding="utf-8"), "hello whisper\n")
            self.assertEqual(generated.read_text(encoding="utf-8"), "existing transcript\n")

    def test_openai_whisper_removes_generated_transcript_when_write_fails(self) -> None:
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
                mock.patch("speed_of_cinnamon.transcriber._run_transcriber_process", side_effect=fake_run),
                mock.patch("speed_of_cinnamon.transcriber._write_text_atomic", side_effect=TranscriptionError("write failed")),
            ):
                with self.assertRaisesRegex(TranscriptionError, "write failed"):
                    transcribe_with_openai_whisper(audio, "en", text)

            self.assertFalse(generated.exists())

    def test_openai_whisper_keeps_primary_error_when_cleanup_fails(self) -> None:
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

            with (
                mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper"),
                mock.patch("speed_of_cinnamon.transcriber._run_transcriber_process", side_effect=fake_run),
                mock.patch("speed_of_cinnamon.transcriber._write_text_atomic", side_effect=TranscriptionError("write failed")),
                mock.patch("pathlib.Path.unlink", side_effect=OSError("cleanup failed")),
            ):
                with self.assertRaisesRegex(TranscriptionError, "write failed"):
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
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with (
            mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper"),
            mock.patch("speed_of_cinnamon.transcriber._run_transcriber_process", side_effect=fake_run),
        ):
            _run_limited_process(["whisper", "audio"])

        self.assertEqual(calls[0][0], "/usr/bin/whisper")

    def test_run_limited_process_filters_dangerous_environment_variables(self) -> None:
        captured_env: dict[str, str] = {}

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            env = kwargs.get("env")
            if isinstance(env, dict):
                captured_env.update(env)
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
            mock.patch("speed_of_cinnamon.transcriber._run_transcriber_process", side_effect=fake_run),
        ):
            self.assertIsNone(_run_limited_process(["whisper", "audio"]))

        self.assertNotIn("LD_PRELOAD", captured_env)
        self.assertNotIn("PYTHONPATH", captured_env)
        self.assertEqual(captured_env["DISPLAY"], ":0")
        self.assertEqual(captured_env["XDG_RUNTIME_DIR"], "/run/user/1000")
        self.assertEqual(captured_env["DBUS_SESSION_BUS_ADDRESS"], "unix:path=/run/user/1000/bus")
        self.assertEqual(captured_env["PATH"], "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")

    def test_run_limited_process_returns_redacted_exit_code_error(self) -> None:
        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            command = args[0] if args else kwargs["args"]
            return subprocess.CompletedProcess(
                command,
                17,
                stdout=b"secret transcript text\n",
                stderr=b"Bearer sk-secret token=abc123\n",
            )

        with (
            mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper"),
            mock.patch("speed_of_cinnamon.transcriber._run_transcriber_process", side_effect=fake_run),
        ):
            with self.assertRaisesRegex(TranscriptionError, "transcriber command failed: exit code 17") as raised:
                _run_limited_process(["whisper", "audio"])

        message = str(raised.exception)
        self.assertNotIn("secret transcript", message)
        self.assertNotIn("Bearer", message)
        self.assertNotIn("sk-secret", message)
        self.assertNotIn("token=abc123", message)

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
            if not isinstance(command, list):
                command = list(command)  # type: ignore[assignment]
            assert isinstance(command, list)
            calls.append((tuple(command), kwargs))
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with (
            mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper"),
            mock.patch("speed_of_cinnamon.transcriber._run_transcriber_process", side_effect=fake_run),
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
                mock.patch("speed_of_cinnamon.transcriber._run_transcriber_process", side_effect=FileNotFoundError("missing")),
            ):
                with self.assertRaisesRegex(TranscriptionError, "is not available"):
                    transcribe_with_openai_whisper(audio, "en", text)

    def test_openai_whisper_fails_closed_if_audio_is_swapped_after_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            replacement = Path(tmp) / "replacement.wav"
            replacement.write_bytes(b"replacement")
            text = Path(tmp) / "sample.txt"
            real_validate = transcriber_module.validate_audio_file

            def validate_and_swap(path: Path) -> Path:
                validated = real_validate(path)
                audio.unlink()
                audio.symlink_to(replacement)
                return validated

            with (
                mock.patch("speed_of_cinnamon.transcriber.validate_audio_file", side_effect=validate_and_swap),
                mock.patch("speed_of_cinnamon.transcriber._command_path", return_value="/usr/bin/whisper"),
                mock.patch(
                    "speed_of_cinnamon.transcriber._run_limited_process",
                    side_effect=AssertionError("backend executed"),
                ),
            ):
                with self.assertRaisesRegex(TranscriptionError, "failed to snapshot audio file for backend"):
                    transcribe_with_openai_whisper(audio, "en", text)

    def test_openai_whisper_fails_closed_if_audio_is_overwritten_with_same_size_after_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            real_snapshot = transcriber_module._snapshot_private_file

            def snapshot_and_overwrite(path: Path, *, field_name: str, include_hash: bool = False) -> tuple[int, int, int, int, int, int] | tuple[int, int, int, int, int, int, str]:
                snapshot = real_snapshot(path, field_name=field_name, include_hash=include_hash)
                with path.open("r+b") as handle:
                    handle.seek(0)
                    handle.write(b"muted")
                return snapshot

            with (
                mock.patch("speed_of_cinnamon.transcriber._snapshot_private_file", side_effect=snapshot_and_overwrite),
                mock.patch("speed_of_cinnamon.transcriber._command_path", return_value="/usr/bin/whisper"),
                mock.patch(
                    "speed_of_cinnamon.transcriber._run_limited_process",
                    side_effect=AssertionError("backend executed"),
                ),
            ):
                with self.assertRaisesRegex(TranscriptionError, "failed to stage audio file for backend access"):
                    transcribe_with_openai_whisper(audio, "en", text)

    def test_staged_audio_file_for_local_backend_is_private(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            snapshot = transcriber_module._snapshot_private_file(
                audio,
                field_name="audio file for backend",
                include_hash=True,
            )

            with transcriber_module._staged_audio_file_for_local_backend(audio, expected_snapshot=snapshot) as staged:
                staged_path = staged
                mode = staged.stat().st_mode & 0o777
                data = staged.read_bytes()

            staged_exists_after_context = staged_path.exists()

        self.assertEqual(data, b"audio")
        self.assertEqual(mode, 0o600)
        self.assertFalse(staged_exists_after_context)

    def test_snapshot_private_file_rejects_oversized_audio_before_hash_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"12345")
            audio.chmod(0o600)
            with (
                mock.patch("speed_of_cinnamon.transcriber.MAX_AUDIO_FILE_BYTES", 4),
                mock.patch("speed_of_cinnamon.transcriber.os.fdopen", side_effect=AssertionError("hash read attempted")),
            ):
                with self.assertRaisesRegex(TranscriptionError, "audio file is too large"):
                    transcriber_module._snapshot_private_file(
                        audio,
                        field_name="audio file for backend",
                        include_hash=True,
                    )

    def test_staged_audio_rejects_oversized_expected_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            audio.chmod(0o600)
            oversized_snapshot = (1, 1, 0o100600, 1, 5, 0, "digest")
            with mock.patch("speed_of_cinnamon.transcriber.MAX_AUDIO_FILE_BYTES", 4):
                with self.assertRaisesRegex(TranscriptionError, "audio file is too large"):
                    with transcriber_module._staged_audio_file_for_local_backend(
                        audio,
                        expected_snapshot=oversized_snapshot,
                    ):
                        pass

    def test_staged_audio_cleanup_failure_is_visible_after_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            snapshot = transcriber_module._snapshot_private_file(
                audio,
                field_name="audio file for backend",
                include_hash=True,
            )

            with self.assertRaisesRegex(TranscriptionError, "failed to clean up staged audio directory"):
                with transcriber_module._staged_audio_file_for_local_backend(audio, expected_snapshot=snapshot) as staged:
                    (staged.parent / "leftover").write_bytes(b"leftover")

    def test_staged_audio_cleanup_reports_failure_after_backend_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            snapshot = transcriber_module._snapshot_private_file(
                audio,
                field_name="audio file for backend",
                include_hash=True,
            )

            with self.assertRaisesRegex(RuntimeError, "backend failed") as raised:
                with transcriber_module._staged_audio_file_for_local_backend(audio, expected_snapshot=snapshot) as staged:
                    (staged.parent / "leftover").write_bytes(b"leftover")
                    raise RuntimeError("backend failed")

            self.assertTrue(
                any(
                    "failed to clean up staged audio directory" in note
                    for note in getattr(raised.exception, "__notes__", [])
                )
            )

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
                mock.patch("speed_of_cinnamon.transcriber._run_transcriber_process", side_effect=fake_run) as mocked_run,
            ):
                result = transcribe_with_whisper_cpp(audio, "de", text, str(model))

            self.assertEqual(result, "hallo cinnamon")
            self.assertEqual(text.read_text(encoding="utf-8").strip(), "hallo cinnamon")
            self.assertFalse(generated.exists())
            command = mocked_run.call_args.args[0]
            self.assertEqual(
                command[:6],
                ["/usr/bin/pwcpp", "-m", str(model), "--language", "de", "-otxt"],
            )
            self.assertTrue(Path(command[-1]).name == audio.name)

    def test_whisper_cpp_restores_existing_pwcpp_sidecar_after_writing(self) -> None:
        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            generated.write_text("hallo cinnamon\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            generated = Path(tmp) / "sample.wav.txt"
            generated.write_text("existing sidecar\n", encoding="utf-8")
            text = Path(tmp) / "sample.txt"
            model = Path(tmp) / "ggml-base.bin"
            model.write_bytes(b"model")
            with (
                mock.patch("speed_of_cinnamon.transcriber.resolve_whisper_cpp_command", return_value="pwcpp"),
                mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/pwcpp"),
                mock.patch("speed_of_cinnamon.transcriber._run_transcriber_process", side_effect=fake_run),
            ):
                result = transcribe_with_whisper_cpp(audio, "de", text, str(model))

            self.assertEqual(result, "hallo cinnamon")
            self.assertEqual(text.read_text(encoding="utf-8").strip(), "hallo cinnamon")
            self.assertEqual(generated.read_text(encoding="utf-8"), "existing sidecar\n")

    def test_whisper_cpp_removes_generated_text_path_when_not_writing(self) -> None:
        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            text.write_text("hallo cinnamon\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            model = Path(tmp) / "ggml-base.bin"
            model.write_bytes(b"model")
            with (
                mock.patch("speed_of_cinnamon.transcriber.resolve_whisper_cpp_command", return_value="whisper-cli"),
                mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper-cli"),
                mock.patch("speed_of_cinnamon.transcriber._run_transcriber_process", side_effect=fake_run),
            ):
                result = transcribe_with_whisper_cpp(audio, "de", text, str(model), write_transcript=False)

            self.assertEqual(result, "hallo cinnamon")
            self.assertFalse(text.exists())

    def test_whisper_cpp_fails_when_cleanup_fails_with_write_transcript_false(self) -> None:
        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            text.write_text("hallo cinnamon\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            model = Path(tmp) / "ggml-base.bin"
            model.write_bytes(b"model")

            with (
                mock.patch("speed_of_cinnamon.transcriber.resolve_whisper_cpp_command", return_value="whisper-cli"),
                mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper-cli"),
                mock.patch("speed_of_cinnamon.transcriber._run_transcriber_process", side_effect=fake_run),
                mock.patch(
                    "speed_of_cinnamon.transcriber._remove_generated_transcript_file",
                    side_effect=TranscriptionError("failed to remove generated transcript"),
                ),
            ):
                with self.assertRaisesRegex(
                    TranscriptionError,
                    "failed to remove generated transcript",
                ):
                    transcribe_with_whisper_cpp(audio, "de", text, str(model), write_transcript=False)

    def test_whisper_cpp_keeps_existing_text_path_when_not_writing(self) -> None:
        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            text.write_text("hallo cinnamon\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            text.write_text("alt", encoding="utf-8")
            model = Path(tmp) / "ggml-base.bin"
            model.write_bytes(b"model")
            with (
                mock.patch("speed_of_cinnamon.transcriber.resolve_whisper_cpp_command", return_value="whisper-cli"),
                mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper-cli"),
                mock.patch("speed_of_cinnamon.transcriber._run_transcriber_process", side_effect=fake_run),
            ):
                result = transcribe_with_whisper_cpp(audio, "de", text, str(model), write_transcript=False)

            self.assertEqual(result, "hallo cinnamon")
            self.assertEqual(text.read_text(encoding="utf-8"), "alt")

    def test_whisper_cpp_preserves_existing_text_path_on_error_when_not_writing(self) -> None:
        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            text.write_bytes(b"invalid\x00text")
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            text.write_text("alt", encoding="utf-8")
            model = Path(tmp) / "ggml-base.bin"
            model.write_bytes(b"model")
            with (
                mock.patch("speed_of_cinnamon.transcriber.resolve_whisper_cpp_command", return_value="whisper-cli"),
                mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper-cli"),
                mock.patch("speed_of_cinnamon.transcriber._run_transcriber_process", side_effect=fake_run),
            ):
                with self.assertRaisesRegex(TranscriptionError, "failed to read generated transcript"):
                    transcribe_with_whisper_cpp(audio, "de", text, str(model), write_transcript=False)

            self.assertEqual(text.read_text(encoding="utf-8"), "alt")

    def test_whisper_cpp_preserves_existing_text_path_on_read_error_when_writing(self) -> None:
        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            text.write_bytes(b"invalid\x00text")
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            text.write_text("existing transcript", encoding="utf-8")
            model = Path(tmp) / "ggml-base.bin"
            model.write_bytes(b"model")
            with (
                mock.patch("speed_of_cinnamon.transcriber.resolve_whisper_cpp_command", return_value="whisper-cli"),
                mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper-cli"),
                mock.patch("speed_of_cinnamon.transcriber._run_transcriber_process", side_effect=fake_run),
            ):
                with self.assertRaisesRegex(TranscriptionError, "failed to read generated transcript"):
                    transcribe_with_whisper_cpp(audio, "de", text, str(model))

            self.assertEqual(text.read_text(encoding="utf-8"), "existing transcript")

    def test_whisper_cpp_preserves_existing_text_path_on_validation_error_when_writing(self) -> None:
        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            text.write_text("hallo cinnamon\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            text.write_text("alt", encoding="utf-8")
            model = Path(tmp) / "ggml-base.bin"
            model.write_bytes(b"model")
            with (
                mock.patch("speed_of_cinnamon.transcriber.resolve_whisper_cpp_command", return_value="whisper-cli"),
                mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper-cli"),
                mock.patch("speed_of_cinnamon.transcriber.MAX_TRANSCRIPT_TEXT_CHARS", 4),
                mock.patch("speed_of_cinnamon.transcriber._run_transcriber_process", side_effect=fake_run),
            ):
                with self.assertRaisesRegex(TranscriptionError, "transcript is too large"):
                    transcribe_with_whisper_cpp(audio, "de", text, str(model))

            self.assertEqual(text.read_text(encoding="utf-8"), "alt")

    def test_whisper_cpp_fails_closed_if_audio_is_swapped_after_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            replacement = Path(tmp) / "replacement.wav"
            replacement.write_bytes(b"replacement")
            text = Path(tmp) / "sample.txt"
            model = Path(tmp) / "ggml-base.bin"
            model.write_bytes(b"model")
            real_validate = transcriber_module.validate_audio_file

            def validate_and_swap(path: Path) -> Path:
                validated = real_validate(path)
                audio.unlink()
                audio.symlink_to(replacement)
                return validated

            with (
                mock.patch("speed_of_cinnamon.transcriber.validate_audio_file", side_effect=validate_and_swap),
                mock.patch(
                    "speed_of_cinnamon.transcriber.model_supports_language",
                    return_value=True,
                ),
                mock.patch(
                    "speed_of_cinnamon.transcriber.resolve_whisper_cpp_command",
                    return_value="whisper-cli",
                ),
                mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper-cli"),
                mock.patch(
                    "speed_of_cinnamon.transcriber._run_limited_process",
                    side_effect=AssertionError("backend executed"),
                ),
            ):
                with self.assertRaisesRegex(TranscriptionError, "failed to snapshot audio file for backend"):
                    transcribe_with_whisper_cpp(audio, "de", text, str(model))

    def test_command_stdout_is_saved_as_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"not really wav but enough for command-template test")
            text = Path(tmp) / "sample.txt"
            result = transcribe(audio, "en", text, "printf 'hello cinnamon'")
        self.assertEqual(result, "hello cinnamon")
        self.assertFalse(text.exists())

    def test_command_empty_stdout_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            with self.assertRaisesRegex(TranscriptionError, "without transcript"):
                transcribe(audio, "en", text, "printf ''")
        self.assertFalse(text.exists())

    def test_command_does_not_receive_personalization_environment_without_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            result = transcribe(
                audio,
                "en",
                text,
                "python3 -c \"import os; print(os.environ.get('SPEED_OF_CINNAMON_VOCABULARY', 'missing'))\"",
                personal_context="Use project terms.",
                vocabulary="PipeWire\nCinnamon",
            )
        self.assertEqual(result, "missing")

    def test_command_receives_personalization_through_explicit_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            result = transcribe(
                audio,
                "en",
                text,
                "printf {vocabulary}",
                personal_context="Use project terms.",
                vocabulary="PipeWire",
            )
        self.assertEqual(result, "PipeWire")

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

    def test_transcribe_with_openai_compatible_api_posts_audio(self) -> None:
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
        self.assertEqual(result, "hello api")
        self.assertFalse(text_path.exists())
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

    def test_transcribe_with_openai_compatible_api_uses_environment_api_key(self) -> None:
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
            captured["headers"] = dict(request.header_items())
            return Response()

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text_path = Path(tmp) / "sample.txt"
            with (
                mock.patch("speed_of_cinnamon.transcriber._open_http_request", side_effect=fake_open_http_request),
                mock.patch.dict(
                    "speed_of_cinnamon.transcriber.os.environ",
                    {"SPEED_OF_CINNAMON_OPENAI_COMPATIBLE_API_KEY": "env-secret"},
                ),
            ):
                result = transcribe(
                    audio,
                    "de",
                    text_path,
                    backend="openai-compatible",
                    openai_compatible_model="whisper-large-v3",
                    openai_compatible_url="http://127.0.0.1:8000/v1",
                )

        self.assertEqual(result, "hello api")
        headers = captured["headers"]
        self.assertEqual(headers["Authorization"], "Bearer env-secret")

    def test_transcribe_with_openai_compatible_api_does_not_write_transcript(self) -> None:
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
        mocked_write.assert_not_called()

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

    def test_openai_compatible_api_fails_when_transcription_flex_is_rejected_without_opt_in(self) -> None:
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
                    io.BytesIO(b'{"error":{"message":"service_tier not enabled for this project","type":"invalid_request_error"}}'),
                )
            return Response()

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text_path = Path(tmp) / "sample.txt"
            with mock.patch("speed_of_cinnamon.transcriber._open_http_request", side_effect=fake_open_http_request):
                with self.assertRaisesRegex(TranscriptionError, "OpenAI-compatible speech API failed \\(400\\)"):
                    transcribe(
                        audio,
                        "de",
                        text_path,
                        backend="openai-compatible",
                        openai_compatible_model="gpt-4o-transcribe",
                        openai_compatible_url="https://api.openai.com/v1",
                        openai_compatible_api_key="secret",
                    )
        self.assertEqual(len(requests), 1)
        self.assertIn(b'name="service_tier"', requests[0].data)

    def test_openai_compatible_api_uses_bytearray_body_for_fallback_request(self) -> None:
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

        requests: list[object] = []

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
                    openai_compatible_service_tier_fallback=True,
                )
        self.assertEqual(result, "hello fallback")
        self.assertEqual(len(requests), 2)
        self.assertIsInstance(requests[0].data, bytearray)
        self.assertIsInstance(requests[1].data, bytearray)
        self.assertIn(b"audio", requests[0].data)
        self.assertIn(b"audio", requests[1].data)

    def test_multipart_form_data_returns_bytearray_without_final_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio_path = Path(tmp) / "sample.wav"
            audio_path.write_bytes(b"audio")
            body, _boundary = _multipart_form_data(
                {"model": "whisper-1", "language": "en", "response_format": "json"},
                "file",
                audio_path,
            )
        self.assertIsInstance(body, bytearray)
        self.assertIn(b"audio", body)

    def test_multipart_form_data_rejects_malformed_utf8_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio_path = Path(tmp) / "sample.wav"
            audio_path.write_bytes(b"audio")
            with self.assertRaisesRegex(TranscriptionError, "invalid UTF-8"):
                _multipart_form_data(
                    {"model": "\ud800", "language": "en", "response_format": "json"},
                    "file",
                    audio_path,
                )

    def test_multipart_form_data_rejects_symlink_audio_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target.wav"
            target.write_bytes(b"audio")
            audio_path = Path(tmp) / "sample.wav"
            audio_path.symlink_to(target)
            with self.assertRaisesRegex(TranscriptionError, "failed to read audio file for API upload"):
                _multipart_form_data({"model": "whisper-1", "language": "en", "response_format": "json"}, "file", audio_path)

    def test_openai_compatible_api_revalidates_audio_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            real_dir = Path(tmp) / "real"
            real_dir.mkdir()
            audio = real_dir / "sample.wav"
            audio.write_bytes(b"audio")
            link_dir = Path(tmp) / "link"
            link_dir.symlink_to(real_dir, target_is_directory=True)
            with self.assertRaisesRegex(TranscriptionError, "must not pass through a symlink"):
                transcribe_with_openai_compatible_api(
                    link_dir / "sample.wav",
                    "en",
                    Path(tmp) / "sample.txt",
                    "gpt-4o-transcribe",
                    "https://api.openai.com/v1",
                )

    def test_openai_compatible_api_rejects_audio_path_swap_between_validation_and_upload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            replacement = Path(tmp) / "replacement.wav"
            replacement.write_bytes(b"replacement")
            snapshot_calls: list[tuple[Path, str]] = []
            real_snapshot = transcriber_module._snapshot_private_file

            def snapshot_and_swap(path: Path, *, field_name: str, include_hash: bool = False) -> tuple[int, int, int, int, int, int] | tuple[
                int, int, int, int, int, int, str
            ]:
                snapshot_calls.append((path, field_name))
                snapshot = real_snapshot(path, field_name=field_name, include_hash=include_hash)
                replacement.replace(path)
                return snapshot

            def blocked_request(*_args: object, **_kwargs: object) -> object:
                self.fail("request should not be made after upload path swap")

            with (
                mock.patch(
                    "speed_of_cinnamon.transcriber._snapshot_private_file",
                    side_effect=snapshot_and_swap,
                ),
                mock.patch(
                    "speed_of_cinnamon.transcriber._open_http_request",
                    side_effect=blocked_request,
                ),
            ):
                with self.assertRaisesRegex(TranscriptionError, "changed between validation and read"):
                    transcribe_with_openai_compatible_api(
                        audio,
                        "de",
                        Path(tmp) / "sample.txt",
                        "gpt-4o-transcribe",
                        "https://api.openai.com/v1",
                    )
        self.assertEqual(snapshot_calls, [(audio, "audio file for API upload")])

    def test_openai_compatible_api_rejects_same_size_in_place_audio_mutation_between_validation_and_upload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            snapshot_calls: list[tuple[Path, str]] = []
            real_snapshot = transcriber_module._snapshot_private_file

            def snapshot_and_mutate(path: Path, *, field_name: str, include_hash: bool = False) -> tuple[int, int, int, int, int, int] | tuple[
                int, int, int, int, int, int, str
            ]:
                snapshot_calls.append((path, field_name))
                snapshot = real_snapshot(path, field_name=field_name, include_hash=include_hash)
                with path.open("r+b") as handle:
                    handle.seek(0)
                    handle.write(b"muted")
                return snapshot

            with (
                mock.patch(
                    "speed_of_cinnamon.transcriber._snapshot_private_file",
                    side_effect=snapshot_and_mutate,
                ),
                mock.patch(
                    "speed_of_cinnamon.transcriber._open_http_request",
                    side_effect=AssertionError("request should not be made after audio mutation"),
                ),
            ):
                with self.assertRaisesRegex(TranscriptionError, "changed between validation and read"):
                    transcribe_with_openai_compatible_api(
                        audio,
                        "de",
                        Path(tmp) / "sample.txt",
                        "gpt-4o-transcribe",
                        "https://api.openai.com/v1",
                    )
        self.assertEqual(snapshot_calls, [(audio, "audio file for API upload")])

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

    def test_openai_compatible_api_rejects_model_with_c1_control_character(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            with self.assertRaisesRegex(TranscriptionError, "multipart form field contains invalid control character"):
                transcribe(
                    audio,
                    "en",
                    Path(tmp) / "sample.txt",
                    backend="openai-compatible",
                    openai_compatible_model="gpt-4o-transcribe\x85",
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

    def test_openai_compatible_api_rejects_escaped_c1_control_in_url(self) -> None:
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
                    openai_compatible_url="https://api.openai.com/v1\\x85",
                    openai_compatible_api_key="secret",
                )

    def test_transcriber_control_helpers_reject_c1_controls(self) -> None:
        self.assertTrue(_contains_http_header_control_chars("token\x85tail"))
        self.assertTrue(_contains_http_header_control_chars("token\\x85tail"))
        self.assertTrue(_contains_multipart_control_chars("field\x85tail"))
        self.assertTrue(_contains_multipart_control_chars("field\\x85tail"))

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

    def test_openai_compatible_api_error_never_leaks_body_or_prompt_in_transcription_exception(self) -> None:
        class ErrorBody:
            def __init__(self) -> None:
                self._read = False
                self.closed = False

            def read(self, size: int = -1) -> bytes:
                if self._read:
                    return b""
                self._read = True
                return (
                    b'{"error":{"message":"token sk-secret leaked transcript for Alice prompt:'
                    b' whisper this secret is sensitive","type":"invalid_request_error"}}'
                )

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
            with mock.patch("speed_of_cinnamon.transcriber._open_http_request", side_effect=error):
                with self.assertRaisesRegex(TranscriptionError, "OpenAI-compatible speech API failed \\(401\\).*\\[redacted remote error\\]") as raised:
                    transcribe(
                        audio,
                        "en",
                        Path(tmp) / "sample.txt",
                        backend="openai-compatible",
                        openai_compatible_model="gpt-4o-transcribe",
                        openai_compatible_url="https://api.openai.com/v1",
                    )

            self.assertNotIn("sk-secret", str(raised.exception))
            self.assertNotIn("prompt:", str(raised.exception))
            self.assertNotIn("Alice", str(raised.exception))
            self.assertIn("https://api.openai.com", str(raised.exception))
            self.assertNotIn("/v1/audio/transcriptions", str(raised.exception))

    def test_openai_compatible_api_error_does_not_echo_url_path_secret(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            error = urllib.error.HTTPError(
                "http://127.0.0.1:8000/v1/secret-token/audio/transcriptions",
                401,
                "Unauthorized",
                {},
                io.BytesIO(b'{"error":{"message":"missing API key"}}'),
            )
            with mock.patch("speed_of_cinnamon.transcriber._open_http_request", side_effect=error):
                with self.assertRaisesRegex(TranscriptionError, "OpenAI-compatible speech API failed \\(401\\)") as raised:
                    transcribe(
                        audio,
                        "en",
                        Path(tmp) / "sample.txt",
                        backend="openai-compatible",
                        openai_compatible_model="local-transcriber",
                        openai_compatible_url="http://127.0.0.1:8000/v1/secret-token",
                    )

            self.assertIn("http://127.0.0.1:8000", str(raised.exception))
            self.assertNotIn("secret-token", str(raised.exception))
            self.assertTrue(error.fp.closed)

    def test_openai_compatible_api_network_error_does_not_leak_request_payload(self) -> None:
        class ResponseError(OSError):
            pass

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            with mock.patch(
                "speed_of_cinnamon.transcriber._open_http_request",
                side_effect=ResponseError("transcript secret prompt sk-supersecret"),
            ):
                with self.assertRaisesRegex(TranscriptionError, "is not reachable at .*\\[redacted remote error\\]") as raised:
                    transcribe(
                        audio,
                        "en",
                        Path(tmp) / "sample.txt",
                        backend="openai-compatible",
                        openai_compatible_model="gpt-4o-transcribe",
                        openai_compatible_url="https://api.openai.com/v1",
                        openai_compatible_api_key="secret-key",
                    )

            self.assertNotIn("secret", str(raised.exception))
            self.assertNotIn("sk-supersecret", str(raised.exception))

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
        _validate_same_origin_redirect(
            "https://api.openai.com/v1/audio/transcriptions",
            "https://api.openai.com:443/v1/audio/transcriptions?request=next",
            field_name="OpenAI-compatible speech request",
        )
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

    def test_openai_compatible_api_rejects_malformed_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            with self.assertRaisesRegex(TranscriptionError, "OpenAI-compatible API URL is invalid"):
                transcribe(
                    audio,
                    "en",
                    Path(tmp) / "sample.txt",
                    backend="openai-compatible",
                    openai_compatible_model="gpt-4o-transcribe",
                    openai_compatible_url="https://[::1",
                )

    def test_openai_compatible_api_rejects_remote_plain_http_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            with self.assertRaisesRegex(TranscriptionError, "must use https:// unless host is local loopback"):
                transcribe(
                    audio,
                    "en",
                    Path(tmp) / "sample.txt",
                    backend="openai-compatible",
                    openai_compatible_model="gpt-4o-transcribe",
                    openai_compatible_url="http://api.example.test/v1",
                )

    def test_openai_compatible_api_rejects_url_userinfo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            with self.assertRaisesRegex(TranscriptionError, "must not contain userinfo"):
                transcribe(
                    audio,
                    "en",
                    Path(tmp) / "sample.txt",
                    backend="openai-compatible",
                    openai_compatible_model="gpt-4o-transcribe",
                    openai_compatible_url="https://user:secret@example.com/v1",
                )

    def test_openai_compatible_api_rejects_empty_url_userinfo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            with self.assertRaisesRegex(TranscriptionError, "must not contain userinfo"):
                transcribe(
                    audio,
                    "en",
                    Path(tmp) / "sample.txt",
                    backend="openai-compatible",
                    openai_compatible_model="gpt-4o-transcribe",
                    openai_compatible_url="https://@example.com/v1",
                )

    def test_openai_compatible_api_rejects_url_query_or_fragment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            for url in (
                "https://api.openai.com/v1?token=secret",
                "https://api.openai.com/v1#token",
            ):
                with self.subTest(url=url):
                    with self.assertRaisesRegex(TranscriptionError, "must not contain query or fragment"):
                        transcribe(
                            audio,
                            "en",
                            Path(tmp) / "sample.txt",
                            backend="openai-compatible",
                            openai_compatible_model="gpt-4o-transcribe",
                            openai_compatible_url=url,
                        )

    def test_openai_compatible_api_rejects_invalid_url_port(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            with self.assertRaisesRegex(TranscriptionError, "has invalid port"):
                transcribe(
                    audio,
                    "en",
                    Path(tmp) / "sample.txt",
                    backend="openai-compatible",
                    openai_compatible_model="gpt-4o-transcribe",
                    openai_compatible_url="https://api.openai.com:bad/v1",
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

    def test_faster_whisper_error_detail_is_redacted(self) -> None:
        class WhisperModel:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                raise RuntimeError("private transcript /home/teladi/secret-model token=secret")

        fake_module = type("FakeFasterWhisper", (), {"WhisperModel": WhisperModel})
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text_path = Path(tmp) / "sample.txt"
            model_path = Path(tmp) / "secret-model"
            model_path.mkdir()
            with mock.patch.dict("sys.modules", {"faster_whisper": fake_module}):
                with self.assertRaises(TranscriptionError) as raised:
                    transcriber_module.transcribe_with_faster_whisper(audio, "en", text_path, str(model_path))

        message = str(raised.exception)
        self.assertEqual(message, "faster-whisper failed: error detail redacted")
        self.assertNotIn("secret-model", message)
        self.assertNotIn("private transcript", message)
        self.assertNotIn("token=secret", message)

    def test_faster_whisper_direct_helper_rejects_symlinked_model_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text_path = Path(tmp) / "sample.txt"
            real_model = Path(tmp) / "ct2-model"
            real_model.mkdir()
            model_path = Path(tmp) / "ct2-link"
            model_path.symlink_to(real_model, target_is_directory=True)

            with self.assertRaisesRegex(TranscriptionError, "CTranslate2 model path must not pass through a symlink"):
                transcriber_module.transcribe_with_faster_whisper(audio, "en", text_path, str(model_path))

    def test_faster_whisper_direct_helper_rejects_symlink_inside_model_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text_path = Path(tmp) / "sample.txt"
            model_path = Path(tmp) / "ct2-model"
            model_path.mkdir()
            real_file = Path(tmp) / "real-model.bin"
            real_file.write_bytes(b"model")
            (model_path / "model.bin").symlink_to(real_file)

            with self.assertRaisesRegex(TranscriptionError, "CTranslate2 model path must not pass through a symlink"):
                transcriber_module.transcribe_with_faster_whisper(audio, "en", text_path, str(model_path))

    def test_faster_whisper_direct_helper_rejects_non_regular_model_tree_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text_path = Path(tmp) / "sample.txt"
            model_path = Path(tmp) / "ct2-model"
            model_path.mkdir()
            fifo = model_path / "model.fifo"
            try:
                os.mkfifo(fifo)
            except OSError as exc:
                self.skipTest(f"fifo unavailable: {exc}")

            with self.assertRaisesRegex(TranscriptionError, "CTranslate2 model path contains unsafe file entries"):
                transcriber_module.transcribe_with_faster_whisper(audio, "en", text_path, str(model_path))

    def test_faster_whisper_direct_helper_fails_closed_when_model_tree_scan_fails(self) -> None:
        def walk_fails(*_args: object, **kwargs: object) -> list[tuple[str, list[str], list[str]]]:
            onerror = kwargs.get("onerror")
            if callable(onerror):
                onerror(OSError("permission denied"))
            return []

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text_path = Path(tmp) / "sample.txt"
            model_path = Path(tmp) / "ct2-model"
            model_path.mkdir()
            with mock.patch("speed_of_cinnamon.transcriber.os.walk", side_effect=walk_fails):
                with self.assertRaisesRegex(TranscriptionError, "CTranslate2 model path is invalid"):
                    transcriber_module.transcribe_with_faster_whisper(audio, "en", text_path, str(model_path))

    def test_faster_whisper_direct_helper_requires_model_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text_path = Path(tmp) / "sample.txt"
            model_path = Path(tmp) / "ct2-model.bin"
            model_path.write_bytes(b"model")

            with self.assertRaisesRegex(TranscriptionError, "CTranslate2 model path must be a directory"):
                transcriber_module.transcribe_with_faster_whisper(audio, "en", text_path, str(model_path))

    def test_faster_whisper_empty_segments_are_rejected(self) -> None:
        class Segment:
            text = "   "

        class WhisperModel:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def transcribe(self, *_args: object, **_kwargs: object) -> tuple[list[Segment], object]:
                return [Segment()], object()

        fake_module = type("FakeFasterWhisper", (), {"WhisperModel": WhisperModel})
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text_path = Path(tmp) / "sample.txt"
            model_path = Path(tmp) / "model"
            model_path.mkdir()
            with (
                mock.patch.dict("sys.modules", {"faster_whisper": fake_module}),
                mock.patch("speed_of_cinnamon.transcriber.model_supports_language", return_value=True),
            ):
                with self.assertRaisesRegex(TranscriptionError, "without transcript"):
                    transcriber_module.transcribe_with_faster_whisper(audio, "en", text_path, str(model_path))

        self.assertFalse(text_path.exists())

    def test_faster_whisper_fails_closed_if_audio_is_swapped_after_validation(self) -> None:
        class FakeFasterWhisper:
            class WhisperModel:
                def __init__(self, *_args: object, **_kwargs: object) -> None:
                    raise AssertionError("backend executed")

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            replacement = Path(tmp) / "replacement.wav"
            replacement.write_bytes(b"replacement")
            text_path = Path(tmp) / "sample.txt"
            model = Path(tmp) / "ct2-base-int8"
            model.mkdir()
            real_validate = transcriber_module.validate_audio_file

            def validate_and_swap(path: Path) -> Path:
                validated = real_validate(path)
                audio.unlink()
                audio.symlink_to(replacement)
                return validated

            with (
                mock.patch.dict("sys.modules", {"faster_whisper": FakeFasterWhisper}),
                mock.patch(
                    "speed_of_cinnamon.transcriber.model_supports_language",
                    return_value=True,
                ),
                mock.patch("speed_of_cinnamon.transcriber.validate_audio_file", side_effect=validate_and_swap),
            ):
                with self.assertRaisesRegex(TranscriptionError, "failed to snapshot audio file for backend"):
                    transcriber_module.transcribe_with_faster_whisper(audio, "en", text_path, str(model))

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

    def test_auto_does_not_use_whisper_cpp_for_downloaded_ctranslate2_model(self) -> None:
        def which(command: str, path: str | None = None) -> str | None:
            return "/usr/bin/whisper-cli" if command == "whisper-cli" else None

        with (
            mock.patch("speed_of_cinnamon.transcriber.default_ctranslate2_model_path", return_value="/models/base-int8"),
            mock.patch("speed_of_cinnamon.transcriber.default_whisper_cpp_model_path", return_value=""),
            mock.patch("speed_of_cinnamon.transcriber.faster_whisper_available", return_value=False),
            mock.patch("speed_of_cinnamon.transcriber.shutil.which", side_effect=which),
        ):
            with self.assertRaisesRegex(TranscriptionError, "no transcriber available"):
                resolve_transcriber(TranscriberConfig())

    def test_whisper_cpp_rejects_english_only_model_for_non_english_language(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            text = Path(tmp) / "sample.txt"
            audio.write_bytes(b"audio")
            model = Path(tmp) / "ggml-tiny.en.bin"
            model.write_bytes(b"model")

            with self.assertRaisesRegex(TranscriptionError, "English-only whisper.cpp model"):
                transcribe_with_whisper_cpp(audio, "de", text, str(model))

    def test_whisper_cpp_direct_helper_rejects_missing_model_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            model = Path(tmp) / "missing.bin"

            with self.assertRaisesRegex(TranscriptionError, "whisper\\.cpp model path is missing"):
                transcribe_with_whisper_cpp(audio, "en", text, str(model))

    def test_whisper_cpp_direct_helper_rejects_symlinked_model_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            real_model = Path(tmp) / "ggml-base.bin"
            real_model.write_bytes(b"model")
            model = Path(tmp) / "ggml-link.bin"
            model.symlink_to(real_model)

            with self.assertRaisesRegex(TranscriptionError, "whisper\\.cpp model path must not pass through a symlink"):
                transcribe_with_whisper_cpp(audio, "en", text, str(model))

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

    def test_auto_rejects_symlinked_whisper_model_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            real_model = Path(tmp) / "custom-ct2-model"
            real_model.mkdir()
            model = Path(tmp) / "symlinked-ct2-model"
            model.symlink_to(real_model, target_is_directory=True)
            with (
                mock.patch("speed_of_cinnamon.transcriber.faster_whisper_available", return_value=True),
                mock.patch("speed_of_cinnamon.transcriber.model_backend_for_path", return_value=""),
                mock.patch("speed_of_cinnamon.transcriber.resolve_whisper_cpp_command", return_value="/usr/bin/whisper-cli"),
            ):
                with self.assertRaisesRegex(TranscriptionError, "must not pass through a symlink"):
                    resolve_transcriber(TranscriberConfig(whisper_model=str(model)))

    def test_explicit_faster_whisper_rejects_symlinked_whisper_model_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            real_model = Path(tmp) / "custom-ct2-model"
            real_model.mkdir()
            model = Path(tmp) / "symlinked-ct2-model"
            model.symlink_to(real_model, target_is_directory=True)
            with self.assertRaisesRegex(TranscriptionError, "must not pass through a symlink"):
                resolve_transcriber(TranscriberConfig(backend="faster-whisper", whisper_model=str(model)))

    def test_auto_rejects_symlinked_whisper_model_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            real_model = Path(tmp) / "custom.model"
            real_model.write_bytes(b"dummy")
            model = Path(tmp) / "symlinked.model"
            model.symlink_to(real_model)
            with self.assertRaisesRegex(TranscriptionError, "must not pass through a symlink"):
                resolve_transcriber(TranscriberConfig(whisper_model=str(model)))

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

    def test_resolve_transcriber_rejects_control_character_model(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "whisper model contains invalid control character"):
            resolve_transcriber(TranscriberConfig(backend="auto", whisper_model="\x85custom.bin"))

    def test_resolve_transcriber_rejects_escaped_control_character_model(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "whisper model contains invalid control character"):
            resolve_transcriber(TranscriberConfig(backend="auto", whisper_model="custom\\x85.bin"))

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
