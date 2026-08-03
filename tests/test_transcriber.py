# mypy: ignore-errors
from __future__ import annotations

import os
import io
import fcntl
import hashlib
import http.client
import shutil
import sys
import stat as stat_module
import subprocess
import tempfile
import threading
import time
import traceback
import unittest
import urllib.error
import urllib.request
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from unittest import mock

from speed_of_cinnamon import transcriber as transcriber_module
from speed_of_cinnamon.transcriber import (
    TranscriberConfig,
    TranscriptionError,
    TranscriptionCleanupError,
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
from speed_of_cinnamon.path_safety import ExpectedTarget, ExpectedTargetKind


def _capture_expected_target_for_test(path: Path) -> ExpectedTarget:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        return ExpectedTarget.captured(fd)
    finally:
        os.close(fd)


class TranscriberTest(unittest.TestCase):
    def test_whisper_cpp_candidate_snapshot_uses_one_state_per_iteration(self) -> None:
        new_state = (7, 8, 9, 10, 11, 12)
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            text.write_text("old transcript", encoding="utf-8")
            model = Path(tmp) / "ggml-base.bin"
            model.write_bytes(b"model")

            def fake_run(command: list[str], **kwargs: object) -> None:
                text.write_text("new transcript", encoding="utf-8")

            with (
                mock.patch("speed_of_cinnamon.transcriber.resolve_whisper_cpp_command", return_value="whisper-cli"),
                mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper-cli"),
                mock.patch("speed_of_cinnamon.transcriber._run_limited_process", side_effect=fake_run),
                mock.patch(
                    "speed_of_cinnamon.transcriber._file_state",
                    return_value=new_state,
                ) as file_state,
            ):
                result = transcribe_with_whisper_cpp(audio, "en", text, str(model))

        self.assertEqual(result, "new transcript")
        self.assertEqual(file_state.call_count, 1)

    def test_whisper_cpp_preserves_primary_base_exception_and_cleans_output(self) -> None:
        for exception_type in (KeyboardInterrupt, SystemExit, GeneratorExit):
            with self.subTest(exception_type=exception_type.__name__):
                with tempfile.TemporaryDirectory() as tmp:
                    audio = Path(tmp) / "sample.wav"
                    audio.write_bytes(b"audio")
                    text = Path(tmp) / "sample.txt"
                    model = Path(tmp) / "ggml-base.bin"
                    model.write_bytes(b"model")
                    primary = exception_type("primary failure")

                    def fake_run(command: list[str], **kwargs: object) -> None:
                        text.write_text("transient transcript", encoding="utf-8")
                        raise primary

                    with (
                        mock.patch("speed_of_cinnamon.transcriber.resolve_whisper_cpp_command", return_value="whisper-cli"),
                        mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper-cli"),
                        mock.patch("speed_of_cinnamon.transcriber._run_limited_process", side_effect=fake_run),
                    ):
                        with self.assertRaises(exception_type) as raised:
                            transcribe_with_whisper_cpp(audio, "en", text, str(model))

                    self.assertIs(raised.exception, primary)
                    self.assertFalse(text.exists())

    def test_whisper_cpp_cleanup_interrupt_does_not_replace_primary_exception(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            model = Path(tmp) / "ggml-base.bin"
            model.write_bytes(b"model")
            primary = SystemExit("primary failure")

            def fake_run(command: list[str], **kwargs: object) -> None:
                text.write_text("transient transcript", encoding="utf-8")
                raise primary

            real_remove = transcriber_module._remove_generated_transcript_file

            def fail_transcript_cleanup(path: Path, **kwargs: object) -> None:
                if path == text:
                    raise KeyboardInterrupt("cleanup failure")
                real_remove(path, **kwargs)

            with (
                mock.patch("speed_of_cinnamon.transcriber.resolve_whisper_cpp_command", return_value="whisper-cli"),
                mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper-cli"),
                mock.patch("speed_of_cinnamon.transcriber._run_limited_process", side_effect=fake_run),
                mock.patch(
                    "speed_of_cinnamon.transcriber._remove_generated_transcript_file",
                    side_effect=fail_transcript_cleanup,
                ),
            ):
                with self.assertRaises(SystemExit) as raised:
                    transcribe_with_whisper_cpp(audio, "en", text, str(model))

        self.assertIs(raised.exception, primary)
        self.assertIn("transcript cleanup failed", getattr(raised.exception, "__notes__", []))

    def test_openai_whisper_preserves_primary_base_exception_and_cleans_output(self) -> None:
        for exception_type in (KeyboardInterrupt, SystemExit, GeneratorExit):
            with self.subTest(exception_type=exception_type.__name__):
                with tempfile.TemporaryDirectory() as tmp:
                    audio = Path(tmp) / "sample.wav"
                    audio.write_bytes(b"audio")
                    text = Path(tmp) / "sample.txt"
                    primary = exception_type("primary failure")

                    def fake_run(command: list[str], **kwargs: object) -> None:
                        text.write_text("transient transcript", encoding="utf-8")
                        raise primary

                    with (
                        mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper"),
                        mock.patch("speed_of_cinnamon.transcriber._run_transcriber_process", side_effect=fake_run),
                    ):
                        with self.assertRaises(exception_type) as raised:
                            transcribe_with_openai_whisper(audio, "en", text)

                    self.assertIs(raised.exception, primary)
                    self.assertFalse(text.exists())

    def test_restore_existing_file_snapshot_requires_valid_nofollow_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.txt"
            path.write_text("existing transcript", encoding="utf-8")
            expected_target = _capture_expected_target_for_test(path)
            for flag in (None, 0, -1, "invalid"):
                with self.subTest(flag=flag):
                    with (
                        mock.patch.object(transcriber_module.os, "O_NOFOLLOW", flag, create=True),
                        mock.patch("speed_of_cinnamon.transcriber.os.open", wraps=os.open) as opened,
                    ):
                        with self.assertRaisesRegex(TranscriptionError, "secure .*open is not supported"):
                            transcriber_module._restore_existing_file_snapshot(
                                path,
                                b"restored transcript",
                                expected_target=expected_target,
                            )
                    opened.assert_not_called()

    def test_transcript_cleanup_rejects_target_replaced_before_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.txt"
            path.write_bytes(b"original")
            expected_target = _capture_expected_target_for_test(path)
            path.unlink()
            path.write_bytes(b"foreign replacement")

            with self.assertRaises(TranscriptionError):
                transcriber_module._remove_generated_transcript_file(
                    path,
                    expected_target=expected_target,
                )

            self.assertEqual(path.read_bytes(), b"foreign replacement")

    def test_transcript_cleanup_rejects_same_size_in_place_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.txt"
            path.write_bytes(b"original")
            expected_target = _capture_expected_target_for_test(path)
            original_times = path.stat()
            path.write_bytes(b"mutated!")
            os.utime(path, ns=(original_times.st_atime_ns, original_times.st_mtime_ns))

            with self.assertRaises(TranscriptionError):
                transcriber_module._remove_generated_transcript_file(
                    path,
                    expected_target=expected_target,
                )

            self.assertEqual(path.read_bytes(), b"mutated!")

    def test_missing_target_claim_never_clobbers_appeared_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.txt"
            expected_target = ExpectedTarget.missing()
            path.write_bytes(b"appeared later")

            with self.assertRaises(TranscriptionError):
                transcriber_module._restore_existing_file_snapshot(
                    path,
                    b"snapshot",
                    expected_target=expected_target,
                )

            self.assertEqual(path.read_bytes(), b"appeared later")

    def test_unknown_target_claim_never_mutates_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.txt"
            path.write_bytes(b"foreign")

            with self.assertRaises(TranscriptionError):
                transcriber_module._restore_existing_file_snapshot(
                    path,
                    b"snapshot",
                    expected_target=ExpectedTarget.unknown(),
                )
            with self.assertRaises(TranscriptionError):
                transcriber_module._remove_generated_transcript_file(
                    path,
                    expected_target=ExpectedTarget.unknown(),
                )

            self.assertEqual(path.read_bytes(), b"foreign")

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

    def test_openai_compatible_rejects_oversized_language_before_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"

            with mock.patch("speed_of_cinnamon.transcriber._open_http_request", side_effect=AssertionError("http request attempted")):
                with self.assertRaisesRegex(TranscriptionError, "language is too large"):
                    transcribe_with_openai_compatible_api(
                        audio,
                        "x" * (transcriber_module.MAX_LANGUAGE_CODE_CHARS + 1),
                        text,
                        model="gpt-4o-transcribe",
                        url="http://127.0.0.1:8000/v1",
                    )

    def test_openai_compatible_rejects_language_control_character_before_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"

            with mock.patch("speed_of_cinnamon.transcriber._open_http_request", side_effect=AssertionError("http request attempted")):
                with self.assertRaisesRegex(TranscriptionError, "invalid control character"):
                    transcribe_with_openai_compatible_api(
                        audio,
                        "de\r\nbad",
                        text,
                        model="gpt-4o-transcribe",
                        url="http://127.0.0.1:8000/v1",
                    )

    def test_direct_openai_api_validates_options_before_audio_or_output_filesystem(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing_audio = root / "missing.wav"
            cases = (
                (
                    "model type",
                    {"model": None},  # type: ignore[dict-item]
                    {},
                ),
                (
                    "url type",
                    {"url": None},  # type: ignore[dict-item]
                    {},
                ),
                (
                    "api key",
                    {"api_key": "secret\x00"},
                    {},
                ),
                (
                    "flex type",
                    {"flex_processing": 1},  # type: ignore[dict-item]
                    {},
                ),
                (
                    "fallback type",
                    {"openai_compatible_service_tier_fallback": 1},  # type: ignore[dict-item]
                    {},
                ),
                (
                    "environment api key",
                    {"api_key": ""},
                    {"SPEED_OF_CINNAMON_OPENAI_COMPATIBLE_API_KEY": "env\nsecret"},
                ),
            )
            for index, (label, overrides, environment) in enumerate(cases):
                with self.subTest(label=label):
                    output = root / f"direct-invalid-{index}" / "result.txt"
                    options: dict[str, object] = {
                        "model": "gpt-4o-transcribe",
                        "url": "https://api.openai.com/v1",
                        "api_key": "secret",
                        "flex_processing": True,
                        "openai_compatible_service_tier_fallback": False,
                    }
                    options.update(overrides)
                    with mock.patch.dict(
                        transcriber_module.os.environ,
                        environment,
                        clear=False,
                    ):
                        with self.assertRaises(TranscriptionError) as raised:
                            transcribe_with_openai_compatible_api(
                                missing_audio,
                                "en",
                                output,
                                **options,  # type: ignore[arg-type]
                            )
                    self.assertFalse(output.parent.exists())
                    self.assertIsNone(raised.exception.__cause__)
                    self.assertIsNone(raised.exception.__context__)

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

    def test_render_command_template_rejects_invalid_template_and_text_path_types(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "template must be text"):
            render_command_template(
                None,  # type: ignore[arg-type]
                Path("/tmp/audio.wav"),
                "en",
                Path("/tmp/out.txt"),
            )
        with self.assertRaisesRegex(TranscriptionError, "text path must be a Path"):
            render_command_template(
                "printf ok",
                Path("/tmp/audio.wav"),
                "en",
                "out.txt",  # type: ignore[arg-type]
            )

    def test_backend_helpers_reject_non_boolean_write_transcript(self) -> None:
        cases = (
            lambda: transcribe_with_openai_whisper(Path("sample.wav"), "en", Path("sample.txt"), write_transcript="false"),  # type: ignore[arg-type]
            lambda: transcribe_with_whisper_cpp(Path("sample.wav"), "en", Path("sample.txt"), "model.bin", write_transcript=1),  # type: ignore[arg-type]
            lambda: transcriber_module.transcribe_with_faster_whisper(Path("sample.wav"), "en", Path("sample.txt"), "model", write_transcript=0),  # type: ignore[arg-type]
            lambda: transcribe_with_openai_compatible_api(
                Path("sample.wav"),
                "en",
                Path("sample.txt"),
                "model",
                "https://example.test/v1",
                write_transcript="no",  # type: ignore[arg-type]
            ),
        )
        for call in cases:
            with self.subTest(call=call):
                with self.assertRaisesRegex(TranscriptionError, "write_transcript must be a boolean"):
                    call()

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

    def test_template_rejects_oversized_command_before_rendering(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "command template is too large"):
            render_command_template(
                "x" * (transcriber_module.MAX_TRANSCRIBER_TEXT_CHARS + 1),
                Path("/tmp/audio.wav"),
                "en",
                Path("/tmp/out.txt"),
            )

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            with mock.patch(
                "speed_of_cinnamon.transcriber.validate_audio_file",
                side_effect=AssertionError("audio validation should not run"),
            ):
                with self.assertRaisesRegex(TranscriptionError, "command template is too large"):
                    transcribe_with_template(
                        "x" * (transcriber_module.MAX_TRANSCRIBER_TEXT_CHARS + 1),
                        audio,
                        "en",
                        Path(tmp) / "out.txt",
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
            expected_target = _capture_expected_target_for_test(generated)

            _remove_generated_transcript_file(
                generated,
                field_name="generated transcript",
                expected_target=expected_target,
            )

            self.assertFalse(generated.exists())

    def test_remove_generated_transcript_file_rejects_fifo_without_blocking(self) -> None:
        if not hasattr(os, "mkfifo"):
            self.skipTest("mkfifo unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            fifo = Path(tmp) / "generated.txt"
            os.mkfifo(fifo)

            with self.assertRaisesRegex(TranscriptionError, "failed to remove"):
                _remove_generated_transcript_file(
                    fifo,
                    field_name="generated transcript",
                    expected_target=ExpectedTarget.unknown(),
                )

    def test_remove_generated_transcript_file_fsyncs_parent_after_delete(self) -> None:
        fsync_modes: list[int] = []
        real_fsync = os.fsync

        def record_fsync(fd: int) -> None:
            fsync_modes.append(os.fstat(fd).st_mode)
            real_fsync(fd)

        with tempfile.TemporaryDirectory() as tmp:
            generated = Path(tmp) / "generated.txt"
            generated.write_text("temporary", encoding="utf-8")
            expected_target = _capture_expected_target_for_test(generated)

            with mock.patch("speed_of_cinnamon.transcriber.os.fsync", side_effect=record_fsync):
                _remove_generated_transcript_file(
                    generated,
                    field_name="generated transcript",
                    expected_target=expected_target,
                )

            self.assertFalse(generated.exists())

        self.assertTrue(any(stat_module.S_ISDIR(mode) for mode in fsync_modes))

    def test_remove_generated_transcript_file_rejects_path_swap_before_delete(self) -> None:
        real_unlink = transcriber_module.unlink_file_if_identity

        def unlink_after_swap(
            path: Path,
            expected_target: ExpectedTarget,
            *,
            field_name: str,
        ) -> bool:
            path.unlink()
            path.write_text("attacker", encoding="utf-8")
            return real_unlink(path, expected_target, field_name=field_name)

        with tempfile.TemporaryDirectory() as tmp:
            generated = Path(tmp) / "generated.txt"
            generated.write_text("temporary", encoding="utf-8")
            expected_target = _capture_expected_target_for_test(generated)

            with mock.patch(
                "speed_of_cinnamon.transcriber.unlink_file_if_identity",
                side_effect=unlink_after_swap,
            ):
                with self.assertRaisesRegex(TranscriptionError, "failed to remove"):
                    _remove_generated_transcript_file(
                        generated,
                        field_name="generated transcript",
                        expected_target=expected_target,
                    )

            self.assertTrue(generated.exists())
            self.assertEqual(generated.read_text(encoding="utf-8"), "attacker")

    def test_remove_generated_transcript_file_rejects_hardlinked_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            seed = Path(tmp) / "seed.txt"
            generated = Path(tmp) / "generated.txt"
            seed.write_text("seed content", encoding="utf-8")
            expected_target = _capture_expected_target_for_test(seed)
            os.link(seed, generated)

            with self.assertRaisesRegex(TranscriptionError, "failed to remove"):
                _remove_generated_transcript_file(
                    generated,
                    field_name="generated transcript",
                    expected_target=expected_target,
                )

            self.assertTrue(seed.exists())
            self.assertTrue(generated.exists())

    def test_remove_generated_transcript_file_rejects_symlink_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            external = Path(tmp) / "external.txt"
            generated = Path(tmp) / "generated.txt"
            external.write_text("outside", encoding="utf-8")
            generated.write_text("owned", encoding="utf-8")
            expected_target = _capture_expected_target_for_test(generated)
            generated.unlink()
            generated.symlink_to(external)

            with self.assertRaisesRegex(TranscriptionError, "failed to remove"):
                _remove_generated_transcript_file(
                    generated,
                    field_name="generated transcript",
                    expected_target=expected_target,
                )

            self.assertTrue(external.exists())
            self.assertEqual(external.read_text(encoding="utf-8"), "outside")

    def test_remove_generated_transcript_file_rejects_captured_fifo_or_symlink_replacement(self) -> None:
        cases = ("fifo", "symlink")
        if not hasattr(os, "mkfifo"):
            cases = ("symlink",)

        for replacement_kind in cases:
            with self.subTest(replacement_kind=replacement_kind), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                generated = root / "generated.txt"
                external = root / "external.txt"
                generated.write_bytes(b"owned bytes")
                external.write_bytes(b"foreign bytes")
                expected_target = _capture_expected_target_for_test(generated)
                generated.unlink()
                if replacement_kind == "fifo":
                    os.mkfifo(generated)
                else:
                    generated.symlink_to(external)

                with self.assertRaisesRegex(TranscriptionError, "failed to remove"):
                    _remove_generated_transcript_file(
                        generated,
                        field_name="generated transcript",
                        expected_target=expected_target,
                    )

                self.assertTrue(generated.exists() or generated.is_symlink())
                self.assertEqual(external.read_bytes(), b"foreign bytes")

    def test_transcribe_reuses_one_preflight_audio_snapshot_per_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "sample.wav"
            audio.write_bytes(b"audio")
            cases = (
                ("whisper", "transcribe_with_openai_whisper", {"backend": "whisper"}),
                ("whisper-cpp", "transcribe_with_whisper_cpp", {"backend": "whisper-cpp"}),
                ("faster-whisper", "transcribe_with_faster_whisper", {"backend": "faster-whisper"}),
                ("openai-compatible", "transcribe_with_openai_compatible_api", {"backend": "openai-compatible"}),
            )
            for backend, helper_name, options in cases:
                with self.subTest(backend=backend):
                    text = root / backend / "sample.txt"
                    call_options = dict(options)
                    if backend == "whisper-cpp":
                        model = root / "ggml-base.bin"
                        model.write_bytes(b"model")
                        call_options["whisper_model"] = str(model)
                    elif backend == "faster-whisper":
                        model = root / "ct2-model"
                        model.mkdir(exist_ok=True)
                        call_options["whisper_model"] = str(model)
                    elif backend == "openai-compatible":
                        call_options.update(
                            {
                                "openai_compatible_model": "whisper-large-v3",
                                "openai_compatible_url": "http://127.0.0.1:8000/v1",
                            }
                        )
                    received: dict[str, object] = {}

                    def backend_stub(*args: object, **kwargs: object) -> str:
                        received.update(kwargs)
                        return "ok transcript"

                    with ExitStack() as stack:
                        snapshot_mock = stack.enter_context(
                            mock.patch(
                                "speed_of_cinnamon.transcriber._snapshot_private_file",
                                wraps=transcriber_module._snapshot_private_file,
                            )
                        )
                        stack.enter_context(
                            mock.patch(
                                f"speed_of_cinnamon.transcriber.{helper_name}",
                                side_effect=backend_stub,
                            )
                        )
                        if backend == "whisper":
                            stack.enter_context(
                                mock.patch(
                                    "speed_of_cinnamon.transcriber._command_path",
                                    return_value="/usr/bin/whisper",
                                )
                            )
                        elif backend == "whisper-cpp":
                            stack.enter_context(
                                mock.patch(
                                    "speed_of_cinnamon.transcriber.resolve_whisper_cpp_command",
                                    return_value="whisper-cli",
                                )
                            )
                            stack.enter_context(
                                mock.patch(
                                    "speed_of_cinnamon.transcriber.model_supports_language",
                                    return_value=True,
                                )
                            )
                        elif backend == "faster-whisper":
                            stack.enter_context(
                                mock.patch(
                                    "speed_of_cinnamon.transcriber.model_supports_language",
                                    return_value=True,
                                )
                            )
                            stack.enter_context(
                                mock.patch(
                                    "speed_of_cinnamon.transcriber.faster_whisper_available",
                                    return_value=True,
                                )
                            )

                        result = transcribe(audio, "en", text, **call_options)

                    self.assertEqual(result, "ok transcript")
                    self.assertEqual(snapshot_mock.call_count, 1)
                    self.assertIn("_expected_audio_snapshot", received)
                    expected_snapshot = received["_expected_audio_snapshot"]
                    self.assertIsInstance(expected_snapshot, tuple)
                    self.assertEqual(len(expected_snapshot), 6)

    def test_transcribe_maps_missing_whisper_command_before_output_filesystem(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "sample.wav"
            audio.write_bytes(b"audio")
            text = root / "nested" / "sample.txt"
            with mock.patch(
                "speed_of_cinnamon.transcriber._command_path",
                side_effect=TranscriptionError("whisper is not available"),
            ):
                with self.assertRaisesRegex(
                    TranscriptionError,
                    "OpenAI whisper command is not installed",
                ):
                    transcribe(audio, "en", text, backend="whisper")
            self.assertFalse(text.parent.exists())

    def test_transcribe_checks_faster_whisper_availability_before_output_filesystem(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "sample.wav"
            audio.write_bytes(b"audio")
            model = root / "ct2-model"
            model.mkdir()
            text = root / "nested" / "sample.txt"
            with (
                mock.patch(
                    "speed_of_cinnamon.transcriber.model_supports_language",
                    return_value=True,
                ),
                mock.patch(
                    "speed_of_cinnamon.transcriber.faster_whisper_available",
                    return_value=False,
                ),
                mock.patch(
                    "speed_of_cinnamon.transcriber.transcribe_with_faster_whisper",
                    side_effect=AssertionError("backend must not run"),
                ),
            ):
                with self.assertRaisesRegex(
                    TranscriptionError,
                    "faster-whisper is not available",
                ):
                    transcribe(
                        audio,
                        "en",
                        text,
                        backend="faster-whisper",
                        whisper_model=str(model),
                    )
            self.assertFalse(text.parent.exists())

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

    def test_template_reads_whisper_output_dir_sidecar_and_cleans_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "sample.wav"
            audio.write_bytes(b"audio")
            text = root / "result.txt"
            template = (
                "python3 -c \"from pathlib import Path; import sys; "
                "Path(sys.argv[1], 'sample.txt').write_text('file transcript')\" {output_dir}"
            )
            result = transcribe_with_template(template, audio, "en", text)

            self.assertEqual(result, "file transcript")
            self.assertFalse(root.joinpath("sample.txt").exists())

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

    @mock.patch("speed_of_cinnamon.transcriber._read_text_file_with_target", side_effect=TranscriptionError("failed to read generated transcript"))
    def test_template_read_error_is_hardened(self, _mocked_read: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            text.write_text("old transcript", encoding="utf-8")

            def command_writes_output(*_args: object, **_kwargs: object) -> str:
                text.write_text("generated transcript", encoding="utf-8")
                return "generated transcript"

            with mock.patch("speed_of_cinnamon.transcriber.run_command_chain", side_effect=command_writes_output):
                with self.assertRaisesRegex(TranscriptionError, "failed to read generated transcript"):
                    transcribe_with_template("printf ignored {text}", audio, "en", text)

    def test_template_returns_fd_trusted_identity_after_atomic_replace(self) -> None:
        def command_replaces_output(*_args: object, **_kwargs: object) -> str:
            replacement = text.with_name("replacement.txt")
            text.unlink()
            replacement.write_text("generated transcript\n", encoding="utf-8")
            replacement.replace(text)
            return ""

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            text.write_text("old transcript\n", encoding="utf-8")
            with mock.patch(
                "speed_of_cinnamon.transcriber.run_command_chain",
                side_effect=command_replaces_output,
            ):
                result = transcribe_with_template("printf ignored {text}", audio, "en", text)

            self.assertEqual(result, "generated transcript")
            self.assertEqual(result.output_path, text)
            self.assertIsNotNone(result.output_stat)
            self.assertEqual(result.output_stat.st_ino, text.stat().st_ino)
            self.assertEqual(result.output_stat.st_nlink, 1)

    def test_template_read_file_rejects_invalid_utf8(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            text.write_text("old transcript", encoding="utf-8")

            def command_writes_invalid(*_args: object, **_kwargs: object) -> str:
                text.write_bytes(b"\xff")
                return "generated transcript"

            with mock.patch("speed_of_cinnamon.transcriber.run_command_chain", side_effect=command_writes_invalid):
                with self.assertRaisesRegex(TranscriptionError, "failed to read generated transcript"):
                    transcribe_with_template("printf ignored {text}", audio, "en", text)

    def test_template_read_file_rejects_escaped_x00(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            text.write_text("old transcript", encoding="utf-8")

            def command_writes_escaped_null(*_args: object, **_kwargs: object) -> str:
                text.write_text("line\\\\x00end", encoding="utf-8")
                return "generated transcript"

            with mock.patch("speed_of_cinnamon.transcriber.run_command_chain", side_effect=command_writes_escaped_null):
                with self.assertRaisesRegex(TranscriptionError, "failed to read generated transcript"):
                    transcribe_with_template("printf ignored {text}", audio, "en", text)

    def test_template_with_text_placeholder_rejects_symlinked_transcript_parent_before_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            real_dir = Path(tmp) / "real-transcripts"
            real_dir.mkdir()
            link_dir = Path(tmp) / "link-transcripts"
            link_dir.symlink_to(real_dir, target_is_directory=True)
            with mock.patch("speed_of_cinnamon.transcriber.run_command_chain") as mocked_run:
                with self.assertRaisesRegex(TranscriptionError, "transcript path must not pass through a symlink"):
                    transcribe_with_template("printf ignored {text}", audio, "en", link_dir / "sample.txt")

        self.assertFalse(mocked_run.called)

    def test_template_direct_helper_validates_audio_path_before_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real_audio = root / "real.wav"
            real_audio.write_bytes(b"audio")
            symlinked_audio = root / "sample.wav"
            symlinked_audio.symlink_to(real_audio)
            with mock.patch("speed_of_cinnamon.transcriber.run_command_chain") as mocked_run:
                with self.assertRaisesRegex(TranscriptionError, "audio path must not pass through a symlink"):
                    transcribe_with_template("printf fake", symlinked_audio, "en", root / "result.txt")
            mocked_run.assert_not_called()

            with mock.patch("speed_of_cinnamon.transcriber.run_command_chain") as mocked_run:
                with self.assertRaisesRegex(TranscriptionError, "audio file is missing or empty"):
                    transcribe_with_template("printf fake", root / "missing.wav", "en", root / "result.txt")
            mocked_run.assert_not_called()

    def test_backend_entrypoints_preflight_before_output_directory_creation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "sample.wav"
            audio.write_bytes(b"audio")

            with self.subTest(entry_point="template"):
                text = root / "template-case" / "result.txt"
                with self.assertRaisesRegex(TranscriptionError, "personal context must be text"):
                    transcribe_with_template(
                        "printf fake --output-dir {output_dir}",
                        audio,
                        "en",
                        text,
                        personal_context=None,  # type: ignore[arg-type]
                    )
                self.assertFalse(text.parent.exists())

            with self.subTest(entry_point="openai-whisper"):
                text = root / "whisper-case" / "result.txt"
                with mock.patch(
                    "speed_of_cinnamon.transcriber._command_path",
                    side_effect=TranscriptionError("OpenAI whisper command is not installed"),
                ):
                    with self.assertRaisesRegex(TranscriptionError, "OpenAI whisper command is not installed"):
                        transcribe_with_openai_whisper(audio, "en", text)
                self.assertFalse(text.parent.exists())

            with self.subTest(entry_point="whisper-cpp"):
                text = root / "whisper-cpp-case" / "result.txt"
                with self.assertRaisesRegex(TranscriptionError, "whisper.cpp model path is missing"):
                    transcribe_with_whisper_cpp(
                        audio,
                        "en",
                        text,
                        str(root / "missing-model.bin"),
                    )
                self.assertFalse(text.parent.exists())

            with self.subTest(entry_point="faster-whisper"):
                text = root / "faster-case" / "result.txt"
                with self.assertRaisesRegex(TranscriptionError, "CTranslate2 model path is missing"):
                    transcriber_module.transcribe_with_faster_whisper(
                        audio,
                        "en",
                        text,
                        str(root / "missing-model"),
                    )
                self.assertFalse(text.parent.exists())

            with self.subTest(entry_point="openai-compatible"):
                text = root / "openai-case" / "result.txt"
                with self.assertRaisesRegex(TranscriptionError, "API URL"):
                    transcribe_with_openai_compatible_api(
                        audio,
                        "en",
                        text,
                        "gpt-4o-transcribe",
                        "not-a-url",
                    )
                self.assertFalse(text.parent.exists())

            with self.subTest(entry_point="transcribe"):
                text = root / "top-level-case" / "result.txt"
                with self.assertRaisesRegex(TranscriptionError, "whisper.cpp model path is missing"):
                    transcribe(
                        audio,
                        "en",
                        text,
                        backend="whisper-cpp",
                        whisper_model=str(root / "missing-top-level-model.bin"),
                    )
                self.assertFalse(text.parent.exists())

    def test_template_direct_helper_rejects_non_path_text_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            with mock.patch("speed_of_cinnamon.transcriber.run_command_chain") as mocked_run:
                with self.assertRaisesRegex(TranscriptionError, "text path must be a Path"):
                    transcribe_with_template("printf fake", audio, "en", "result.txt")  # type: ignore[arg-type]
            mocked_run.assert_not_called()

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
                mock.patch("speed_of_cinnamon.transcriber.split_command_chain", return_value=[("printf",)]),
                mock.patch("speed_of_cinnamon.transcriber.run_command_chain", side_effect=command_fails),
            ):
                with self.assertRaisesRegex(TranscriptionError, "command failed"):
                    transcribe_with_template("printf ignored {text}", audio, "en", text)

            self.assertEqual(text.read_text(encoding="utf-8"), "old")

    def test_template_with_text_placeholder_rejects_unchanged_existing_text_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            text.write_text("stale transcript\n", encoding="utf-8")
            with mock.patch("speed_of_cinnamon.transcriber.run_command_chain", return_value="command output"):
                with self.assertRaisesRegex(TranscriptionError, "did not update the transcript file"):
                    transcribe_with_template("printf ignored {text}", audio, "en", text)
            self.assertEqual(text.read_text(encoding="utf-8"), "stale transcript\n")

    def test_template_with_text_placeholder_restores_existing_text_path_when_success_removes_it(self) -> None:
        def command_removes_file(*_args: object, **_kwargs: object) -> str:
            text.unlink()
            return "command output"

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            text.write_text("previous transcript\n", encoding="utf-8")
            with mock.patch("speed_of_cinnamon.transcriber.run_command_chain", side_effect=command_removes_file):
                result = transcribe_with_template("printf ignored {text}", audio, "en", text)
            self.assertEqual(result, "command output")
            self.assertEqual(text.read_text(encoding="utf-8"), "previous transcript\n")

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
                mock.patch("speed_of_cinnamon.transcriber.split_command_chain", return_value=[("printf",)]),
                mock.patch("speed_of_cinnamon.transcriber.run_command_chain", side_effect=command_fails),
            ):
                with self.assertRaisesRegex(TranscriptionError, "transcriber command failed: exit code 127; command output redacted") as raised:
                    transcribe_with_template("printf ignored {text}", audio, "en", text)

            message = str(raised.exception)
            self.assertIn("transcriber command failed", message)
            self.assertNotIn("secret transcript", message)
            self.assertNotIn("stderr", message)
            self.assertNotIn("sk-leak", message)
            self.assertNotIn("api-key", message)
            self.assertEqual(text.read_text(encoding="utf-8"), "old")

    def test_template_with_command_error_redacts_path_details(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            text.write_text("old", encoding="utf-8")
            for raw_error, expected in (
                (
                    "transcriber command not found: /home/private/custom-whisper",
                    "transcriber command not found",
                ),
                (
                    "transcriber command execution failed: /tmp/private/api-key=secret",
                    "transcriber command execution failed",
                ),
            ):
                with self.subTest(raw_error=raw_error):
                    with (
                        mock.patch("speed_of_cinnamon.transcriber.split_command_chain", return_value=[("printf",)]),
                        mock.patch(
                            "speed_of_cinnamon.transcriber.run_command_chain",
                            side_effect=CommandChainError(raw_error),
                        ),
                    ):
                        with self.assertRaises(TranscriptionError) as raised:
                            transcribe_with_template("printf ignored {text}", audio, "en", text)

                    self.assertEqual(str(raised.exception), expected)
                    self.assertNotIn("/home/private", str(raised.exception))
                    self.assertNotIn("/tmp/private", str(raised.exception))
                    self.assertNotIn("secret", str(raised.exception))
                    self.assertEqual(text.read_text(encoding="utf-8"), "old")

    def test_template_command_error_has_no_raw_exception_metadata(self) -> None:
        secret = "/srv/private/command-secret token=abc123"
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            with (
                mock.patch("speed_of_cinnamon.transcriber.split_command_chain", return_value=[("printf",)]),
                mock.patch(
                    "speed_of_cinnamon.transcriber.run_command_chain",
                    side_effect=CommandChainError(secret),
                ),
            ):
                with self.assertRaises(TranscriptionError) as raised:
                    transcribe_with_template("printf ignored {text}", audio, "en", text)

            error = raised.exception
            self.assertIsNone(error.__cause__)
            self.assertIsNone(error.__context__)
            rendered = "\n".join(
                (
                    str(error),
                    repr(error),
                    repr(error.__cause__),
                    repr(error.__context__),
                    repr(getattr(error, "__notes__", ())),
                )
            )
            self.assertNotIn(secret, rendered)
            self.assertNotIn("abc123", rendered)

    def test_template_success_cleanup_baseexception_preserves_type_without_retry(self) -> None:
        for exception_type in (KeyboardInterrupt, SystemExit):
            with self.subTest(exception_type=exception_type.__name__):
                with tempfile.TemporaryDirectory() as tmp:
                    audio = Path(tmp) / "sample.wav"
                    audio.write_bytes(b"audio")
                    text = Path(tmp) / "result.txt"
                    sidecar = Path(tmp) / "sample.txt"
                    cleanup_calls: list[Path] = []
                    real_remove = transcriber_module._remove_generated_transcript_file
                    interrupt = exception_type("cleanup interrupted")

                    def command_writes_sidecar(*_args: object, **_kwargs: object) -> str:
                        sidecar.write_text("generated transcript\n", encoding="utf-8")
                        return "generated transcript"

                    def fail_cleanup(
                        path: Path,
                        *,
                        field_name: str = "generated transcript",
                        expected_target: ExpectedTarget,
                    ) -> None:
                        if path != sidecar:
                            real_remove(
                                path,
                                field_name=field_name,
                                expected_target=expected_target,
                            )
                            return
                        cleanup_calls.append(path)
                        raise interrupt

                    with (
                        mock.patch(
                            "speed_of_cinnamon.transcriber.run_command_chain",
                            side_effect=command_writes_sidecar,
                        ),
                        mock.patch(
                            "speed_of_cinnamon.transcriber._remove_generated_transcript_file",
                            side_effect=fail_cleanup,
                        ),
                    ):
                        with self.assertRaises(exception_type) as raised:
                            transcribe_with_template("printf ignored {output_dir}", audio, "en", text)

                    self.assertIsNot(raised.exception, interrupt)
                    self.assertEqual(str(raised.exception), "transcription cleanup interrupted")
                    self.assertIsNone(raised.exception.__cause__)
                    self.assertIsNone(raised.exception.__context__)
                    self.assertEqual(cleanup_calls, [sidecar])

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
                mock.patch("speed_of_cinnamon.transcriber.split_command_chain", return_value=[("printf",)]),
                mock.patch("speed_of_cinnamon.transcriber.run_command_chain", side_effect=command_writes_invalid),
            ):
                with self.assertRaisesRegex(TranscriptionError, "failed to read generated transcript"):
                    transcribe_with_template("printf ignored {text}", audio, "en", text)

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
                mock.patch("speed_of_cinnamon.transcriber.split_command_chain", return_value=[("printf",)]),
                mock.patch("speed_of_cinnamon.transcriber.MAX_TRANSCRIPT_TEXT_CHARS", 4),
                mock.patch("speed_of_cinnamon.transcriber.run_command_chain", side_effect=command_writes_long_transcript),
            ):
                with self.assertRaisesRegex(TranscriptionError, "transcript file text is too large"):
                    transcribe_with_template("printf ignored {text}", audio, "en", text)

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
                mock.patch("speed_of_cinnamon.transcriber.split_command_chain", return_value=[("printf",)]),
                mock.patch("speed_of_cinnamon.transcriber.run_command_chain", side_effect=command_writes_invalid),
            ):
                with self.assertRaisesRegex(TranscriptionError, "failed to read generated transcript"):
                    transcribe_with_template("printf ignored {text}", audio, "en", text)

            self.assertFalse(text.exists())

    def test_template_with_text_placeholder_reports_cleanup_error_on_read_error(self) -> None:
        def command_writes_invalid(*_args: object, **_kwargs: object) -> str:
            text.write_bytes(b"invalid\\x00text")
            return "generated transcript"

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            real_remove = transcriber_module._remove_generated_transcript_file

            def fail_cleanup(
                path: Path,
                *,
                field_name: str = "generated transcript",
                expected_target: ExpectedTarget,
            ) -> None:
                if path == text:
                    raise TranscriptionError("failed to remove generated transcript")
                real_remove(path, field_name=field_name, expected_target=expected_target)

            with (
                mock.patch("speed_of_cinnamon.transcriber.split_command_chain", return_value=[("printf",)]),
                mock.patch(
                    "speed_of_cinnamon.transcriber.run_command_chain",
                    side_effect=command_writes_invalid,
                ),
                mock.patch(
                    "speed_of_cinnamon.transcriber._remove_generated_transcript_file",
                    side_effect=fail_cleanup,
                ),
            ):
                with self.assertRaisesRegex(TranscriptionError, "failed to read generated transcript") as raised:
                    transcribe_with_template("printf ignored {text}", audio, "en", text)

        self.assertTrue(
            any(
                "transcript cleanup failed" in note
                for note in getattr(raised.exception, "__notes__", [])
            )
        )

    def test_template_with_text_placeholder_reports_cleanup_error_on_command_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            real_remove = transcriber_module._remove_generated_transcript_file

            def fail_cleanup(
                path: Path,
                *,
                field_name: str = "generated transcript",
                expected_target: ExpectedTarget,
            ) -> None:
                if path == text:
                    raise TranscriptionError("failed to remove generated transcript")
                real_remove(path, field_name=field_name, expected_target=expected_target)

            with (
                mock.patch("speed_of_cinnamon.transcriber.split_command_chain", return_value=[("printf",)]),
                mock.patch(
                    "speed_of_cinnamon.transcriber.run_command_chain",
                    side_effect=CommandChainError("command failed"),
                ),
                mock.patch(
                    "speed_of_cinnamon.transcriber._remove_generated_transcript_file",
                    side_effect=fail_cleanup,
                ),
            ):
                with self.assertRaisesRegex(TranscriptionError, "command failed") as raised:
                    transcribe_with_template("printf ignored {text}", audio, "en", text)

            self.assertTrue(
                any(
                    "transcript cleanup failed" in note
                    for note in getattr(raised.exception, "__notes__", [])
                )
            )

    def test_template_cleanup_retries_after_precommit_mutation_failure(self) -> None:
        real_remove = transcriber_module._remove_generated_transcript_file
        remove_calls: list[Path] = []

        def command_writes_sidecar(*_args: object, **_kwargs: object) -> str:
            sidecar.write_text("generated transcript\n", encoding="utf-8")
            return "generated transcript"

        def fail_once(
            path: Path,
            *,
            field_name: str = "generated transcript",
            expected_target: ExpectedTarget,
        ) -> None:
            if path != sidecar:
                real_remove(path, field_name=field_name, expected_target=expected_target)
                return
            remove_calls.append(path)
            if len(remove_calls) == 1:
                raise TranscriptionError("transient cleanup failure")
            real_remove(path, field_name=field_name, expected_target=expected_target)

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "result.txt"
            sidecar = Path(tmp) / "sample.txt"
            with (
                mock.patch("speed_of_cinnamon.transcriber.run_command_chain", side_effect=command_writes_sidecar),
                mock.patch(
                    "speed_of_cinnamon.transcriber._remove_generated_transcript_file",
                    side_effect=fail_once,
                ),
            ):
                result = transcribe_with_template("printf ignored {output_dir}", audio, "en", text)

            self.assertEqual(result, "generated transcript")
            self.assertEqual(remove_calls, [sidecar, sidecar])
            self.assertFalse(sidecar.exists())

    def test_template_cleanup_retry_after_postcommit_error_preserves_new_target(self) -> None:
        real_remove = transcriber_module._remove_generated_transcript_file
        remove_calls: list[Path] = []

        def command_writes_sidecar(*_args: object, **_kwargs: object) -> str:
            sidecar.write_text("generated transcript\n", encoding="utf-8")
            return "generated transcript"

        def fail_after_unlink(
            path: Path,
            *,
            field_name: str = "generated transcript",
            expected_target: ExpectedTarget,
        ) -> None:
            if path != sidecar:
                real_remove(path, field_name=field_name, expected_target=expected_target)
                return
            remove_calls.append(path)
            if len(remove_calls) == 1:
                path.unlink()
                path.write_text("new target\n", encoding="utf-8")
                raise TranscriptionError("postcommit cleanup failure")
            real_remove(path, field_name=field_name, expected_target=expected_target)

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "result.txt"
            sidecar = Path(tmp) / "sample.txt"
            with (
                mock.patch("speed_of_cinnamon.transcriber.run_command_chain", side_effect=command_writes_sidecar),
                mock.patch(
                    "speed_of_cinnamon.transcriber._remove_generated_transcript_file",
                    side_effect=fail_after_unlink,
                ),
            ):
                with self.assertRaisesRegex(TranscriptionCleanupError, "failed to clean up generated transcript"):
                    transcribe_with_template("printf ignored {output_dir}", audio, "en", text)

            self.assertEqual(remove_calls, [sidecar, sidecar])
            self.assertEqual(sidecar.read_text(encoding="utf-8"), "new target\n")

    def test_template_restore_retry_after_replacement_preserves_foreign_target(self) -> None:
        real_restore = transcriber_module._restore_existing_file_snapshot
        restore_calls: list[Path] = []

        def command_replaces_sidecar(*_args: object, **_kwargs: object) -> str:
            sidecar.write_text("generated transcript\n", encoding="utf-8")
            return "generated transcript"

        def replace_before_retry(
            path: Path,
            snapshot: bytes,
            *,
            expected_target: ExpectedTarget,
        ) -> None:
            restore_calls.append(path)
            if len(restore_calls) == 1:
                path.unlink()
                path.write_text("foreign target\n", encoding="utf-8")
                raise TranscriptionError("restore interrupted")
            real_restore(path, snapshot, expected_target=expected_target)

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "result.txt"
            sidecar = Path(tmp) / "sample.txt"
            sidecar.write_text("old transcript\n", encoding="utf-8")
            with (
                mock.patch("speed_of_cinnamon.transcriber.run_command_chain", side_effect=command_replaces_sidecar),
                mock.patch(
                    "speed_of_cinnamon.transcriber._restore_existing_file_snapshot",
                    side_effect=replace_before_retry,
                ),
            ):
                with self.assertRaisesRegex(TranscriptionCleanupError, "failed to clean up generated transcript"):
                    transcribe_with_template("printf ignored {output_dir}", audio, "en", text)

            self.assertEqual(restore_calls, [sidecar, sidecar])
            self.assertEqual(sidecar.read_text(encoding="utf-8"), "foreign target\n")

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

    def test_path_expansion_errors_are_transcription_errors(self) -> None:
        unknown_user_path = Path("~speed_of_cinnamon_user_that_does_not_exist_9f2/sample.wav")
        with self.assertRaisesRegex(TranscriptionError, "audio path is invalid"):
            validate_audio_file(unknown_user_path)
        with self.assertRaisesRegex(TranscriptionError, "text path is invalid"):
            transcriber_module._normalize_transcript_path(unknown_user_path.with_name("result.txt"))

    def test_model_path_expansion_errors_are_transcription_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            with self.assertRaisesRegex(TranscriptionError, "whisper\\.cpp model path is invalid"):
                transcribe_with_whisper_cpp(
                    audio,
                    "en",
                    Path(tmp) / "sample.txt",
                    "~speed_of_cinnamon_model_user_that_does_not_exist_9f2/model.bin",
                )

    def test_resolve_model_path_expansion_errors_are_transcription_errors(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "configured whisper model path is invalid"):
            resolve_transcriber(
                TranscriberConfig(
                    backend="auto",
                    whisper_model="~speed_of_cinnamon_model_user_that_does_not_exist_9f2/model.bin",
                )
            )

    def test_validate_audio_file_rejects_non_path(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "audio path must be a Path"):
            validate_audio_file("sample.wav")  # type: ignore[arg-type]

    def test_validate_audio_file_normalizes_relative_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            previous_cwd = Path.cwd()
            os.chdir(tmp)
            try:
                normalized = validate_audio_file(Path("sample.wav"))
            finally:
                os.chdir(previous_cwd)

            self.assertEqual(normalized, audio)

    def test_validate_audio_file_rejects_parent_path_component(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "sample.wav"
            audio.write_bytes(b"audio")
            with self.assertRaisesRegex(TranscriptionError, "unsafe path component"):
                validate_audio_file(root / "nested" / ".." / audio.name)

    def test_transcribe_normalizes_relative_transcript_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            previous_cwd = Path.cwd()
            os.chdir(tmp)
            try:
                Path("sample.wav").write_bytes(b"audio")
                result = transcribe(
                    Path("sample.wav"),
                    "en",
                    Path("nested") / "sample.txt",
                    "printf hello",
                )
            finally:
                os.chdir(previous_cwd)

            self.assertEqual(result, "hello")
            self.assertTrue((root / "nested").is_dir())

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

    def test_read_text_file_target_valueerror_is_sanitized(self) -> None:
        secret = "/srv/private/read-secret token=abc123"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "generated.txt"
            path.write_text("transcript", encoding="utf-8")
            with mock.patch.object(ExpectedTarget, "captured", side_effect=ValueError(secret)):
                with self.assertRaisesRegex(TranscriptionError, "failed to read generated transcript") as raised:
                    transcriber_module._read_text_file_with_target(path)

            error = raised.exception
            self.assertIsNone(error.__cause__)
            self.assertIsNone(error.__context__)
            rendered = "\n".join(
                (
                    str(error),
                    repr(error),
                    repr(error.__cause__),
                    repr(error.__context__),
                    repr(getattr(error, "__notes__", ())),
                )
            )
            self.assertNotIn(secret, rendered)
            self.assertNotIn("abc123", rendered)

    def test_snapshot_existing_file_target_valueerror_is_sanitized(self) -> None:
        secret = "/srv/private/snapshot-secret token=abc123"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "existing.txt"
            path.write_text("existing transcript", encoding="utf-8")
            with mock.patch.object(ExpectedTarget, "captured", side_effect=ValueError(secret)):
                with self.assertRaisesRegex(
                    TranscriptionError,
                    "failed to snapshot existing transcript file",
                ) as raised:
                    transcriber_module._snapshot_existing_file_with_state(path)

            error = raised.exception
            self.assertIsNone(error.__cause__)
            self.assertIsNone(error.__context__)
            rendered = "\n".join(
                (
                    str(error),
                    repr(error),
                    repr(error.__cause__),
                    repr(error.__context__),
                    repr(getattr(error, "__notes__", ())),
                )
            )
            self.assertNotIn(secret, rendered)
            self.assertNotIn("abc123", rendered)

    def test_snapshot_existing_file_second_block_runtimeerror_is_chain_free_and_closes_fd_once(self) -> None:
        secret = "/srv/private/snapshot-runtime-secret token=abc123"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "existing.txt"
            path.write_text("existing transcript", encoding="utf-8")
            real_open = transcriber_module.open_file_without_following_symlinks
            real_fdopen = transcriber_module.os.fdopen
            opened_fds: list[int] = []
            closed_fds: list[int] = []

            def tracked_open(*args: object, **kwargs: object) -> int:
                fd = real_open(*args, **kwargs)
                opened_fds.append(fd)
                return fd

            class TrackingHandle:
                def __init__(self, handle: object, fd: int) -> None:
                    self._handle = handle
                    self._fd = fd
                    self._closed = False

                def __enter__(self) -> "TrackingHandle":
                    return self

                def __exit__(self, *_args: object) -> bool:
                    self.close()
                    return False

                def read(self, *args: object, **kwargs: object) -> bytes:
                    return self._handle.read(*args, **kwargs)

                def fileno(self) -> int:
                    return self._handle.fileno()

                def close(self) -> None:
                    if not self._closed:
                        self._closed = True
                        closed_fds.append(self._fd)
                        self._handle.close()

            def tracked_fdopen(fd: int, *args: object, **kwargs: object) -> TrackingHandle:
                return TrackingHandle(real_fdopen(fd, *args, **kwargs), fd)

            with (
                mock.patch(
                    "speed_of_cinnamon.transcriber.open_file_without_following_symlinks",
                    side_effect=tracked_open,
                ),
                mock.patch("speed_of_cinnamon.transcriber.os.fdopen", side_effect=tracked_fdopen),
                mock.patch.object(ExpectedTarget, "captured", side_effect=RuntimeError(secret)),
            ):
                with self.assertRaisesRegex(
                    TranscriptionError,
                    "failed to snapshot existing transcript file",
                ) as raised:
                    transcriber_module._snapshot_existing_file_with_state(path)

            self.assertEqual(closed_fds, opened_fds)
            error = raised.exception
            self.assertIsNone(error.__cause__)
            self.assertIsNone(error.__context__)
            rendered = "\n".join(
                (
                    str(error),
                    repr(error),
                    repr(error.__cause__),
                    repr(error.__context__),
                    repr(getattr(error, "__notes__", ())),
                )
            )
            self.assertNotIn(secret, rendered)
            self.assertNotIn("abc123", rendered)

    def test_staged_target_valueerror_is_sanitized(self) -> None:
        secret = "/srv/private/stage-secret token=abc123"
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            with mock.patch(
                "speed_of_cinnamon.transcriber._TargetSnapshot",
                side_effect=ValueError(secret),
            ):
                with self.assertRaisesRegex(
                    TranscriptionError,
                    "failed to stage audio file for backend access",
                ) as raised:
                    with transcriber_module._staged_audio_file_for_local_backend(audio):
                        pass

            error = raised.exception
            self.assertIsNone(error.__cause__)
            self.assertIsNone(error.__context__)
            rendered = "\n".join(
                (
                    str(error),
                    repr(error),
                    repr(error.__cause__),
                    repr(error.__context__),
                    repr(getattr(error, "__notes__", ())),
                )
            )
            self.assertNotIn(secret, rendered)
            self.assertNotIn("abc123", rendered)

    def test_expected_target_evidence_ignores_digest_limit_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.txt"
            path.write_bytes(b"transcript")
            other_path = Path(tmp) / "other.txt"
            other_path.write_bytes(b"different")
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            other_fd = os.open(other_path, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                narrow = ExpectedTarget.captured(
                    fd,
                    require_same_version=True,
                    max_digest_bytes=1024,
                )
                wide = ExpectedTarget.captured(
                    fd,
                    require_same_version=True,
                    max_digest_bytes=2048,
                )
                other = ExpectedTarget.captured(
                    other_fd,
                    require_same_version=True,
                    max_digest_bytes=1024,
                )
                identity_only = ExpectedTarget.captured(fd, require_same_version=False)
            finally:
                os.close(fd)
                os.close(other_fd)

            self.assertTrue(transcriber_module._same_expected_target_evidence(narrow, wide))
            self.assertFalse(transcriber_module._same_expected_target_evidence(narrow, other))
            self.assertFalse(transcriber_module._same_expected_target_evidence(narrow, identity_only))
            self.assertFalse(
                transcriber_module._same_expected_target_evidence(
                    narrow,
                    replace(narrow, content_digest=bytes(byte ^ 0xFF for byte in narrow.content_digest)),
                )
            )
            self.assertFalse(
                transcriber_module._same_expected_target_evidence(
                    narrow,
                    ExpectedTarget.missing(),
                )
            )

    def test_restore_existing_file_error_does_not_leak_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "secret-transcript.txt"
            with mock.patch(
                "speed_of_cinnamon.transcriber.replace_bytes_atomically_if_identity",
                side_effect=OSError(f"restore failed {path}"),
            ):
                with self.assertRaisesRegex(TranscriptionError, "failed to restore existing transcript file") as raised:
                    transcriber_module._restore_existing_file_snapshot(
                        path,
                        b"previous transcript",
                        expected_target=ExpectedTarget.unknown(),
                    )
            self.assertNotIn(str(path), str(raised.exception))

    def test_restore_existing_file_value_error_is_chain_free(self) -> None:
        secret = "/secret/restore-token=abc123"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "transcript.txt"
            with mock.patch(
                "speed_of_cinnamon.transcriber.replace_bytes_atomically_if_identity",
                side_effect=ValueError(secret),
            ):
                with self.assertRaisesRegex(
                    TranscriptionError,
                    "failed to restore existing transcript file",
                ) as raised:
                    transcriber_module._restore_existing_file_snapshot(
                        path,
                        b"previous transcript",
                        expected_target=ExpectedTarget.unknown(),
                    )

        error = raised.exception
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        rendered = "\n".join(
            (
                str(error),
                repr(error),
                repr(error.__cause__),
                repr(error.__context__),
                repr(getattr(error, "__notes__", ())),
            )
        )
        self.assertNotIn(secret, rendered)
        self.assertNotIn("abc123", rendered)

    def test_restore_existing_file_expected_errors_are_chain_free(self) -> None:
        error_types = (OSError, RuntimeError, TypeError, TranscriptionError)
        for error_type in error_types:
            secret = f"/secret/restore-{error_type.__name__}-token=abc123"
            with self.subTest(error_type=error_type.__name__), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "transcript.txt"
                with mock.patch(
                    "speed_of_cinnamon.transcriber.replace_bytes_atomically_if_identity",
                    side_effect=error_type(secret),
                ):
                    with self.assertRaisesRegex(
                        TranscriptionError,
                        "failed to restore existing transcript file",
                    ) as raised:
                        transcriber_module._restore_existing_file_snapshot(
                            path,
                            b"previous transcript",
                            expected_target=ExpectedTarget.unknown(),
                        )

                error = raised.exception
                self.assertIsNone(error.__cause__)
                self.assertIsNone(error.__context__)
                rendered = "\n".join(
                    (
                        str(error),
                        repr(error),
                        repr(error.__cause__),
                        repr(error.__context__),
                        repr(getattr(error, "__notes__", ())),
                    )
                )
                self.assertNotIn(secret, rendered)
                self.assertNotIn("abc123", rendered)

    def test_remove_generated_file_value_error_is_chain_free(self) -> None:
        secret = "/secret/remove-token=abc123"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "transcript.txt"
            with mock.patch(
                "speed_of_cinnamon.transcriber.unlink_file_if_identity",
                side_effect=ValueError(secret),
            ):
                with self.assertRaisesRegex(
                    TranscriptionError,
                    "failed to remove generated transcript",
                ) as raised:
                    transcriber_module._remove_generated_transcript_file(
                        path,
                        expected_target=ExpectedTarget.unknown(),
                    )

        error = raised.exception
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        rendered = "\n".join(
            (
                str(error),
                repr(error),
                repr(error.__cause__),
                repr(error.__context__),
                repr(getattr(error, "__notes__", ())),
            )
        )
        self.assertNotIn(secret, rendered)
        self.assertNotIn("abc123", rendered)

    def test_remove_generated_file_expected_errors_are_chain_free(self) -> None:
        error_types = (OSError, RuntimeError, TypeError, TranscriptionError)
        for error_type in error_types:
            secret = f"/secret/remove-{error_type.__name__}-token=abc123"
            with self.subTest(error_type=error_type.__name__), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "transcript.txt"
                with mock.patch(
                    "speed_of_cinnamon.transcriber.unlink_file_if_identity",
                    side_effect=error_type(secret),
                ):
                    with self.assertRaisesRegex(
                        TranscriptionError,
                        "failed to remove generated transcript",
                    ) as raised:
                        transcriber_module._remove_generated_transcript_file(
                            path,
                            expected_target=ExpectedTarget.unknown(),
                        )

                error = raised.exception
                self.assertIsNone(error.__cause__)
                self.assertIsNone(error.__context__)
                rendered = "\n".join(
                    (
                        str(error),
                        repr(error),
                        repr(error.__cause__),
                        repr(error.__context__),
                        repr(getattr(error, "__notes__", ())),
                    )
                )
                self.assertNotIn(secret, rendered)
                self.assertNotIn("abc123", rendered)

    def test_restore_or_remove_generated_transcript_errors_are_chain_free(self) -> None:
        error_types = (OSError, TranscriptionError)
        for error_type in error_types:
            secret = f"/secret/outer-chain-{error_type.__name__}-token=abc123"
            with self.subTest(error_type=error_type.__name__), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "transcript.txt"
                with mock.patch(
                    "speed_of_cinnamon.transcriber._remove_generated_transcript_file",
                    side_effect=error_type(secret),
                ):
                    with self.assertRaisesRegex(
                        TranscriptionError,
                        "failed to remove generated transcript",
                    ) as raised:
                        transcriber_module._restore_or_remove_generated_transcript(
                            path,
                            None,
                            expected_target=ExpectedTarget.unknown(),
                        )

                error = raised.exception
                self.assertIsNone(error.__cause__)
                self.assertIsNone(error.__context__)
                rendered = "\n".join(
                    (
                        str(error),
                        repr(error),
                        repr(error.__cause__),
                        repr(error.__context__),
                        repr(getattr(error, "__notes__", ())),
                    )
                )
                self.assertNotIn(secret, rendered)
                self.assertNotIn("abc123", rendered)

    def test_restore_or_remove_generated_transcript_ignores_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "transcript.txt"
            with mock.patch(
                "speed_of_cinnamon.transcriber._remove_generated_transcript_file",
                side_effect=FileNotFoundError("/secret/missing-transcript"),
            ):
                transcriber_module._restore_or_remove_generated_transcript(
                    path,
                    None,
                    expected_target=ExpectedTarget.unknown(),
                )

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

    def test_resolve_transcriber_rejects_invalid_config_field_types(self) -> None:
        for config, message in (
            (TranscriberConfig(command_template=None), "command template must be text"),  # type: ignore[arg-type]
            (TranscriberConfig(whisper_model=None), "whisper model must be text"),  # type: ignore[arg-type]
            (TranscriberConfig(language=None), "language must be text"),  # type: ignore[arg-type]
        ):
            with self.subTest(config=config):
                with self.assertRaisesRegex(TranscriptionError, message):
                    resolve_transcriber(config)

    def test_resolve_transcriber_ignores_irrelevant_whisper_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            real_model = Path(tmp) / "model.bin"
            real_model.write_bytes(b"model")
            symlinked_model = Path(tmp) / "model-link.bin"
            symlinked_model.symlink_to(real_model)

            self.assertEqual(
                resolve_transcriber(
                    TranscriberConfig(
                        backend="command",
                        command_template="printf ok",
                        whisper_model=str(symlinked_model),
                    )
                ),
                "command",
            )
            self.assertEqual(
                resolve_transcriber(
                    TranscriberConfig(backend="whisper", whisper_model=str(symlinked_model))
                ),
                "whisper",
            )
            self.assertEqual(
                resolve_transcriber(
                    TranscriberConfig(backend="openai-compatible", whisper_model=str(symlinked_model))
                ),
                "openai-compatible",
            )

            self.assertEqual(
                resolve_transcriber(
                    TranscriberConfig(
                        backend="auto",
                        command_template="printf ok",
                        whisper_model=str(symlinked_model),
                    )
                ),
                "command",
            )

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
                mock.patch("speed_of_cinnamon.transcriber.split_command_chain", return_value=[("printf",)]),
                mock.patch(
                    "speed_of_cinnamon.transcriber.run_command_chain",
                    side_effect=CommandChainError("max_output_chars must be non-negative"),
                ),
            ):
                with self.assertRaisesRegex(TranscriptionError, "max_output_chars must be non-negative"):
                    transcribe_with_template("printf", audio, "en", Path(tmp) / "sample.txt")

    def test_template_rejects_updated_chain_limit_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            with (
                mock.patch("speed_of_cinnamon.transcriber.split_command_chain", return_value=[("printf",)]),
                mock.patch(
                    "speed_of_cinnamon.transcriber.run_command_chain",
                    side_effect=CommandChainError("max_output_chars must be positive"),
                ),
            ):
                with self.assertRaisesRegex(TranscriptionError, "max_output_chars must be positive"):
                    transcribe_with_template("printf", audio, "en", Path(tmp) / "sample.txt")

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

    def test_openai_whisper_rejects_unchanged_existing_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            generated = Path(tmp) / "sample.txt"
            generated.write_text("stale whisper\n", encoding="utf-8")
            with (
                mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper"),
                mock.patch(
                    "speed_of_cinnamon.transcriber._run_transcriber_process",
                    return_value=subprocess.CompletedProcess([], 0, stdout=b"", stderr=b""),
                ),
            ):
                with self.assertRaisesRegex(TranscriptionError, "did not produce a transcript"):
                    transcribe_with_openai_whisper(audio, "en", Path(tmp) / "result.txt")
            self.assertEqual(generated.read_text(encoding="utf-8"), "stale whisper\n")

    def test_openai_whisper_restores_existing_sidecar_when_process_removes_it(self) -> None:
        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            generated.unlink()
            raise CommandChainError("backend failed")

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            generated = Path(tmp) / "sample.txt"
            generated.write_text("previous whisper\n", encoding="utf-8")
            with (
                mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper"),
                mock.patch("speed_of_cinnamon.transcriber._run_transcriber_process", side_effect=fake_run),
            ):
                with self.assertRaisesRegex(TranscriptionError, "transcriber command failed: \\[redacted command error\\]"):
                    transcribe_with_openai_whisper(audio, "en", Path(tmp) / "result.txt")
            self.assertEqual(generated.read_text(encoding="utf-8"), "previous whisper\n")

    def test_openai_whisper_restores_existing_sidecar_when_success_removes_it(self) -> None:
        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            generated.unlink()
            return subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            generated = Path(tmp) / "sample.txt"
            generated.write_text("previous whisper\n", encoding="utf-8")
            with (
                mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper"),
                mock.patch("speed_of_cinnamon.transcriber._run_limited_process", side_effect=fake_run),
            ):
                with self.assertRaisesRegex(TranscriptionError, "did not produce a transcript"):
                    transcribe_with_openai_whisper(audio, "en", Path(tmp) / "result.txt")
            self.assertEqual(generated.read_text(encoding="utf-8"), "previous whisper\n")

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

    def test_openai_whisper_prepares_nested_transcript_directory(self) -> None:
        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            command = args[0] if args else kwargs["args"]
            assert isinstance(command, list)
            output_dir = Path(command[command.index("--output_dir") + 1])
            (output_dir / "sample.txt").write_text("hello whisper\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "sample.wav"
            audio.write_bytes(b"audio")
            text = root / "nested" / "result.txt"
            with (
                mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper"),
                mock.patch("speed_of_cinnamon.transcriber._run_transcriber_process", side_effect=fake_run),
            ):
                result = transcribe_with_openai_whisper(audio, "en", text)

            self.assertEqual(result, "hello whisper")
            self.assertEqual(text.read_text(encoding="utf-8"), "hello whisper\n")

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
                    TranscriptionCleanupError,
                    "failed to clean up generated transcript",
                ):
                    transcribe_with_openai_whisper(audio, "en", text, write_transcript=False)

    def test_openai_whisper_cleans_up_dangling_generated_output_after_backend_error(self) -> None:
        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            command = args[0] if args else kwargs["args"]
            assert isinstance(command, list)
            output_dir = Path(command[command.index("--output_dir") + 1])
            (output_dir / "sample.txt").symlink_to(Path(tmp) / "missing.txt")
            raise CommandChainError("backend failed")

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "result.txt"
            generated = Path(tmp) / "sample.txt"
            with (
                mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper"),
                mock.patch("speed_of_cinnamon.transcriber._run_transcriber_process", side_effect=fake_run),
            ):
                with self.assertRaisesRegex(TranscriptionError, "transcriber command failed: \\[redacted command error\\]") as raised:
                    transcribe_with_openai_whisper(audio, "en", text)

            self.assertTrue(generated.is_symlink())
            self.assertTrue(
                any(
                    "transcript cleanup failed" in note
                    for note in getattr(raised.exception, "__notes__", [])
                )
            )

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
            self.assertIsInstance(result, transcriber_module._TrustedTranscriptText)
            self.assertEqual(result.output_path, text)
            self.assertIsNotNone(result.output_stat)
            self.assertEqual(result.output_stat.st_ino, text.stat().st_ino)
            self.assertEqual(result.output_stat.st_nlink, 1)

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

    def test_openai_whisper_matching_text_path_returns_fd_trusted_output(self) -> None:
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

            self.assertIsInstance(result, transcriber_module._TrustedTranscriptText)
            self.assertEqual(result.output_path, text)
            self.assertIsNotNone(result.output_stat)
            self.assertEqual(result.output_stat.st_ino, text.stat().st_ino)
            self.assertEqual(result.output_stat.st_nlink, 1)

    def test_openai_whisper_matching_text_path_rejects_replacement_before_return(self) -> None:
        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            command = args[0] if args else kwargs["args"]
            assert isinstance(command, list)
            output_dir = Path(command[command.index("--output_dir") + 1])
            (output_dir / "sample.txt").write_text("hello whisper\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        replaced = False
        real_regular_file_stat = transcriber_module._regular_file_stat

        def replace_before_final_stat(path: Path, *, field_name: str) -> os.stat_result | None:
            nonlocal replaced
            if path == text and not replaced:
                replaced = True
                foreign = path.with_name("foreign.txt")
                path.unlink()
                foreign.write_text("foreign output\n", encoding="utf-8")
                foreign.replace(path)
            return real_regular_file_stat(path, field_name=field_name)

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            with (
                mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper"),
                mock.patch("speed_of_cinnamon.transcriber._run_transcriber_process", side_effect=fake_run),
                mock.patch(
                    "speed_of_cinnamon.transcriber._regular_file_stat",
                    side_effect=replace_before_final_stat,
                ),
            ):
                with self.assertRaisesRegex(TranscriptionError, "transcript output changed before return"):
                    transcribe_with_openai_whisper(audio, "en", text)

            self.assertTrue(replaced)
            self.assertEqual(text.read_text(encoding="utf-8"), "foreign output\n")

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

    def test_run_limited_process_redacts_command_setup_error_details(self) -> None:
        for raw_error, expected in (
            (
                "transcriber command not found: /home/private/custom-whisper",
                "transcriber command not found",
            ),
            (
                "transcriber command execution failed: /tmp/private/api-key=secret",
                "transcriber command execution failed",
            ),
        ):
            with self.subTest(raw_error=raw_error):
                with (
                    mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper"),
                    mock.patch(
                        "speed_of_cinnamon.transcriber._run_transcriber_process",
                        side_effect=CommandChainError(raw_error),
                    ),
                ):
                    with self.assertRaises(TranscriptionError) as raised:
                        _run_limited_process(["whisper", "audio"])

                self.assertEqual(str(raised.exception), expected)
                self.assertNotIn("/home/private", str(raised.exception))
                self.assertNotIn("/tmp/private", str(raised.exception))
                self.assertNotIn("secret", str(raised.exception))

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

    def test_staged_audio_allows_large_audio_with_digest_bound_cleanup_and_content_check(self) -> None:
        payload = b"audio" * 250_001
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(payload)
            snapshot = transcriber_module._snapshot_private_file(
                audio,
                field_name="audio file for backend",
                include_hash=True,
            )

            with transcriber_module._staged_audio_file_for_local_backend(audio, expected_snapshot=snapshot) as staged:
                staged_path = staged
                self.assertEqual(hashlib.sha256(staged.read_bytes()).hexdigest(), snapshot[6])

            self.assertFalse(staged_path.exists())

    def test_staged_audio_recaptures_mutated_regular_file_for_cleanup(self) -> None:
        payload = b"audio" * 250_001
        mutated_payload = b"muted" * 250_001
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "sample.wav"
            audio.write_bytes(payload)
            snapshot = transcriber_module._snapshot_private_file(
                audio,
                field_name="audio file for backend",
                include_hash=True,
            )
            staging_dir = root / "staging"

            def test_mkdtemp(*args: object, **kwargs: object) -> str:
                staging_dir.mkdir(mode=0o700)
                return str(staging_dir)

            staged_path: Path | None = None
            staging_exists_after = False
            recaptured_targets: list[ExpectedTarget] = []

            real_capture = transcriber_module._capture_expected_target

            def capture_current_target(
                path: Path,
                *,
                field_name: str,
                max_digest_bytes: int | None = None,
            ) -> ExpectedTarget:
                target = real_capture(
                    path,
                    field_name=field_name,
                    max_digest_bytes=max_digest_bytes,
                )
                recaptured_targets.append(target)
                return target

            try:
                with (
                    mock.patch("speed_of_cinnamon.transcriber.tempfile.mkdtemp", side_effect=test_mkdtemp),
                    mock.patch(
                        "speed_of_cinnamon.transcriber._capture_expected_target",
                        side_effect=capture_current_target,
                    ),
                ):
                    with transcriber_module._staged_audio_file_for_local_backend(
                        audio,
                        expected_snapshot=snapshot,
                    ) as staged:
                        staged_path = staged
                        staged.write_bytes(mutated_payload)

                self.assertIsNotNone(staged_path)
                staging_exists_after = staging_dir.exists()
            finally:
                if staging_dir.exists():
                    shutil.rmtree(staging_dir, ignore_errors=False)

            self.assertFalse(staging_exists_after)
            self.assertFalse(staging_dir.exists())
            self.assertEqual(len(recaptured_targets), 1)
            self.assertEqual(
                recaptured_targets[0].content_digest,
                hashlib.sha256(mutated_payload).digest(),
            )
            self.assertNotEqual(recaptured_targets[0].content_digest, snapshot[6])
            self.assertEqual(recaptured_targets[0].max_digest_bytes, MAX_AUDIO_FILE_BYTES)

    def test_openai_api_error_leaves_no_output_artifacts_inside_tempdir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "sample.wav"
            audio.write_bytes(b"audio")
            output = root / "failed-output" / "sample.txt"
            with mock.patch(
                "speed_of_cinnamon.transcriber._open_http_request",
                side_effect=OSError("/srv/private/remote-token"),
            ):
                with self.assertRaises(TranscriptionError):
                    transcribe(
                        audio,
                        "en",
                        output,
                        backend="openai-compatible",
                        openai_compatible_model="gpt-4o-transcribe",
                        openai_compatible_url="https://api.openai.com/v1",
                    )
            self.assertFalse(output.parent.exists())
            self.assertFalse(any(root.glob("failed-output*")))

    def test_staged_audio_cleanup_preserves_symlink_fifo_and_hardlink_replacements(self) -> None:
        replacements = ("symlink", "fifo", "hardlink")
        for replacement_kind in replacements:
            with self.subTest(replacement_kind=replacement_kind), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                audio = root / "sample.wav"
                audio.write_bytes(b"audio")
                foreign = root / "foreign.txt"
                foreign.write_bytes(b"foreign")
                snapshot = transcriber_module._snapshot_private_file(
                    audio,
                    field_name="audio file for backend",
                    include_hash=True,
                )
                staging_dir = root / f"staging-{replacement_kind}"
                staged_path: Path | None = None

                def test_mkdtemp(*args: object, **kwargs: object) -> str:
                    staging_dir.mkdir(mode=0o700)
                    return str(staging_dir)

                try:
                    with mock.patch(
                        "speed_of_cinnamon.transcriber.tempfile.mkdtemp",
                        side_effect=test_mkdtemp,
                    ):
                        with self.assertRaisesRegex(
                            TranscriptionError,
                            "failed to clean up staged audio file",
                        ):
                            with transcriber_module._staged_audio_file_for_local_backend(
                                audio,
                                expected_snapshot=snapshot,
                            ) as staged:
                                staged_path = staged
                                staged.unlink()
                                if replacement_kind == "symlink":
                                    staged.symlink_to(foreign)
                                elif replacement_kind == "fifo":
                                    os.mkfifo(staged, 0o600)
                                else:
                                    os.link(foreign, staged)

                    self.assertIsNotNone(staged_path)
                    self.assertTrue(staged_path.exists() or staged_path.is_symlink())
                    self.assertEqual(foreign.read_bytes(), b"foreign")
                    if replacement_kind == "symlink":
                        self.assertTrue(staged_path.is_symlink())
                    elif replacement_kind == "fifo":
                        self.assertTrue(stat_module.S_ISFIFO(staged_path.stat().st_mode))
                    else:
                        self.assertEqual(staged_path.stat().st_ino, foreign.stat().st_ino)
                        self.assertEqual(staged_path.stat().st_nlink, 2)
                finally:
                    if staging_dir.exists():
                        shutil.rmtree(staging_dir, ignore_errors=False)

                self.assertFalse(staging_dir.exists())
                self.assertEqual(foreign.read_bytes(), b"foreign")

    def test_snapshot_private_file_uses_one_fd_and_final_stat_after_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"a" * 65_536)
            real_open = transcriber_module.open_file_without_following_symlinks
            real_sha256 = hashlib.sha256
            open_calls = 0
            mutated = False

            def tracked_open(*args: object, **kwargs: object) -> int:
                nonlocal open_calls
                open_calls += 1
                return real_open(*args, **kwargs)

            class MutatingHasher:
                def __init__(self) -> None:
                    self._hasher = real_sha256()

                def update(self, data: bytes) -> None:
                    nonlocal mutated
                    self._hasher.update(data)
                    if not mutated:
                        mutated = True
                        audio.write_bytes(b"b" * 65_536)

                def hexdigest(self) -> str:
                    return self._hasher.hexdigest()

            def mutating_sha256() -> MutatingHasher:
                return MutatingHasher()

            with (
                mock.patch(
                    "speed_of_cinnamon.transcriber.open_file_without_following_symlinks",
                    side_effect=tracked_open,
                ),
                mock.patch("speed_of_cinnamon.transcriber.hashlib.sha256", side_effect=mutating_sha256),
            ):
                with self.assertRaisesRegex(TranscriptionError, "changed while snapshotting"):
                    transcriber_module._snapshot_private_file(
                        audio,
                        field_name="audio file for backend",
                        include_hash=True,
                    )

            self.assertEqual(open_calls, 1)

    def test_snapshot_existing_file_closes_fd_when_fd_validation_raises_oserror(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            text = Path(tmp) / "sample.txt"
            text.write_text("existing transcript\n", encoding="utf-8")
            real_open = transcriber_module.open_file_without_following_symlinks
            opened_fds: list[int] = []

            def tracked_open(*args: object, **kwargs: object) -> int:
                fd = real_open(*args, **kwargs)
                opened_fds.append(fd)
                return fd

            with (
                mock.patch(
                    "speed_of_cinnamon.transcriber.open_file_without_following_symlinks",
                    side_effect=tracked_open,
                ),
                mock.patch(
                    "speed_of_cinnamon.transcriber.assert_fd_is_regular_private_file",
                    side_effect=OSError("validation failed"),
                ),
            ):
                with self.assertRaisesRegex(TranscriptionError, "failed to snapshot existing transcript file"):
                    transcriber_module._snapshot_existing_file_with_state(text)

            self.assertEqual(len(opened_fds), 1)
            with self.assertRaises(OSError):
                os.fstat(opened_fds[0])

    def test_snapshot_existing_file_closes_fd_once_when_fd_validation_reports_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            text = Path(tmp) / "sample.txt"
            text.write_text("existing transcript\n", encoding="utf-8")
            real_open = transcriber_module.open_file_without_following_symlinks
            real_close = os.close
            opened_fds: list[int] = []
            closed_fds: list[int] = []
            close_counts_at_return: dict[int, int] = {}

            def tracked_open(*args: object, **kwargs: object) -> int:
                fd = real_open(*args, **kwargs)
                opened_fds.append(fd)
                close_counts_at_return[fd] = closed_fds.count(fd)
                return fd

            def tracked_close(fd: int) -> None:
                closed_fds.append(fd)
                real_close(fd)

            with (
                mock.patch(
                    "speed_of_cinnamon.transcriber.open_file_without_following_symlinks",
                    side_effect=tracked_open,
                ),
                mock.patch(
                    "speed_of_cinnamon.transcriber.assert_fd_is_regular_private_file",
                    side_effect=FileNotFoundError("file vanished"),
                ),
                mock.patch("speed_of_cinnamon.transcriber.os.close", side_effect=tracked_close),
            ):
                self.assertIsNone(transcriber_module._snapshot_existing_file_with_state(text))

            self.assertEqual(len(opened_fds), 1)
            self.assertEqual(
                closed_fds.count(opened_fds[0]) - close_counts_at_return[opened_fds[0]],
                1,
            )
            with self.assertRaises(OSError):
                os.fstat(opened_fds[0])

    def test_owned_fd_close_errors_fail_closed_without_primary(self) -> None:
        secret = "/secret/internal-close"
        for entry_point in ("regular-stat", "text-read", "private-read", "snapshot"):
            with self.subTest(entry_point=entry_point), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                audio = root / "sample.wav"
                audio.write_bytes(b"audio")
                transcript = root / "transcript.txt"
                transcript.write_text("transcript", encoding="utf-8")
                real_open = transcriber_module.os.open
                real_close = transcriber_module.os.close
                close_calls: list[int] = []
                failed_once = False
                owned_fds: list[int] = []

                def fail_once(fd: int) -> None:
                    nonlocal failed_once
                    close_calls.append(fd)
                    real_close(fd)
                    if not failed_once:
                        failed_once = True
                        raise OSError(secret)

                if entry_point == "regular-stat":
                    call = lambda: transcriber_module._regular_file_stat(
                        transcript,
                        field_name="generated transcript",
                    )
                elif entry_point == "text-read":
                    call = lambda: transcriber_module._read_text_file_with_target(transcript)
                elif entry_point == "private-read":
                    call = lambda: transcriber_module._read_private_file_bytes(
                        audio,
                        field_name="audio file",
                    )
                else:
                    call = lambda: transcriber_module._snapshot_private_file(
                        audio,
                        field_name="audio file",
                        include_hash=False,
                    )

                if entry_point == "snapshot":
                    owned_fds.append(real_open(audio, os.O_RDONLY))
                    fd_patches = (
                        mock.patch(
                            "speed_of_cinnamon.transcriber.open_file_without_following_symlinks",
                            return_value=owned_fds[0],
                        ),
                    )
                else:
                    owned_fds.append(
                        real_open(
                            root,
                            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
                        )
                    )
                    fd_patches = (
                        mock.patch(
                            "speed_of_cinnamon.transcriber.open_directory_without_following_symlinks",
                            return_value=owned_fds[0],
                        ),
                    )

                owned_identities = {
                    fd: (os.fstat(fd).st_dev, os.fstat(fd).st_ino)
                    for fd in owned_fds
                }

                with mock.patch("speed_of_cinnamon.transcriber.assert_no_symlink_ancestors"):
                    with fd_patches[0]:
                        with mock.patch(
                            "speed_of_cinnamon.transcriber.os.close",
                            side_effect=fail_once,
                        ):
                            with self.assertRaisesRegex(
                                TranscriptionError,
                                "failed to release file descriptor",
                            ) as raised:
                                call()

                for fd in owned_fds:
                    try:
                        current_identity = (os.fstat(fd).st_dev, os.fstat(fd).st_ino)
                    except OSError:
                        pass
                    else:
                        if current_identity == owned_identities[fd]:
                            real_close(fd)

                self.assertEqual(len(close_calls), len(set(close_calls)))
                error = raised.exception
                self.assertIsNone(error.__cause__)
                self.assertIsNone(error.__context__)
                rendered = "\n".join(
                    (
                        str(error),
                        repr(error),
                        repr(error.__cause__),
                        repr(error.__context__),
                        repr(getattr(error, "__notes__", ())),
                    )
                )
                self.assertNotIn(secret, rendered)

    def test_staged_audio_preserves_existing_unknown_target_and_primary_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "sample.wav"
            audio.write_bytes(b"audio")
            staging_dir = root / "staging"
            staging_dir.mkdir()
            staging_path = staging_dir / audio.name
            real_open = transcriber_module.os.open
            real_fstat = transcriber_module.os.fstat
            target_fds: set[int] = set()

            def tracked_open(path: object, *args: object, **kwargs: object) -> int:
                fd = real_open(path, *args, **kwargs)
                if Path(path) == staging_path:
                    target_fds.add(fd)
                return fd

            def fail_target_fstat(fd: int) -> os.stat_result:
                if fd in target_fds:
                    raise OSError("staged target stat failed")
                return real_fstat(fd)

            try:
                with (
                    mock.patch(
                        "speed_of_cinnamon.transcriber.tempfile.mkdtemp",
                        return_value=str(staging_dir),
                    ),
                    mock.patch("speed_of_cinnamon.transcriber.os.open", side_effect=tracked_open),
                    mock.patch("speed_of_cinnamon.transcriber.os.fstat", side_effect=fail_target_fstat),
                ):
                    with self.assertRaisesRegex(
                        TranscriptionError,
                        "failed to stage audio file for backend access",
                    ) as raised:
                        with transcriber_module._staged_audio_file_for_local_backend(audio):
                            self.fail("staging setup unexpectedly succeeded")

                self.assertTrue(staging_path.exists())
                self.assertTrue(
                    any(
                        "failed to clean up staged audio file" in note
                        for note in getattr(raised.exception, "__notes__", [])
                    )
                )
            finally:
                if staging_path.exists():
                    staging_path.unlink()
                if staging_dir.exists():
                    staging_dir.rmdir()

    def test_template_restores_existing_generated_output_after_later_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "sample.wav"
            audio.write_bytes(b"audio")
            generated = root / "sample.txt"
            text = root / "result.txt"
            previous = b"previous bytes\x00\xff"
            generated.write_bytes(previous)

            def fake_run(*_args: object, **_kwargs: object) -> str:
                generated.write_bytes(b"new bytes")
                return ""

            real_assert_text_length = transcriber_module._assert_text_length

            def fail_after_output(value: str, *, field_name: str, max_chars: int = MAX_TRANSCRIBER_TEXT_CHARS) -> str:
                if field_name == "transcript file text":
                    raise TranscriptionError("later failure")
                return real_assert_text_length(value, field_name=field_name, max_chars=max_chars)

            with (
                mock.patch("speed_of_cinnamon.transcriber.run_command_chain", side_effect=fake_run) as mocked_run,
                mock.patch(
                    "speed_of_cinnamon.transcriber._assert_text_length",
                    side_effect=fail_after_output,
                ),
            ):
                with self.assertRaisesRegex(TranscriptionError, "later failure"):
                    transcribe_with_template(
                        "printf generated --output-dir {output_dir}",
                        audio,
                        "en",
                        text,
                    )

            mocked_run.assert_called_once()
            self.assertEqual(generated.read_bytes(), previous)

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
            root = Path(tmp)
            audio = root / "sample.wav"
            audio.write_bytes(b"audio")
            snapshot = transcriber_module._snapshot_private_file(
                audio,
                field_name="audio file for backend",
                include_hash=True,
            )
            staging_dir = root / "staging"

            def make_staging_dir(*_args: object, **_kwargs: object) -> str:
                staging_dir.mkdir(mode=0o700)
                return str(staging_dir)

            try:
                with mock.patch("speed_of_cinnamon.transcriber.tempfile.mkdtemp", side_effect=make_staging_dir):
                    with self.assertRaisesRegex(TranscriptionError, "failed to clean up staged audio directory"):
                        with transcriber_module._staged_audio_file_for_local_backend(
                            audio,
                            expected_snapshot=snapshot,
                        ) as staged:
                            (staged.parent / "leftover").write_bytes(b"leftover")
            finally:
                (staging_dir / "leftover").unlink(missing_ok=True)
                staging_dir.rmdir()

    def test_staged_audio_cleanup_oserror_is_chain_free_without_body_error(self) -> None:
        secret = "/secret/stage-cleanup"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "sample.wav"
            audio.write_bytes(b"audio")
            snapshot = transcriber_module._snapshot_private_file(
                audio,
                field_name="audio file for backend",
                include_hash=True,
            )
            staging_dir = root / "staging"

            def make_staging_dir(*_args: object, **_kwargs: object) -> str:
                staging_dir.mkdir()
                return str(staging_dir)

            try:
                with (
                    mock.patch("speed_of_cinnamon.transcriber.tempfile.mkdtemp", side_effect=make_staging_dir),
                    mock.patch(
                        "speed_of_cinnamon.transcriber._remove_generated_transcript_file",
                        side_effect=OSError(secret),
                    ),
                ):
                    with self.assertRaisesRegex(
                        TranscriptionError,
                        "failed to clean up staged audio file",
                    ) as raised:
                        with transcriber_module._staged_audio_file_for_local_backend(
                            audio,
                            expected_snapshot=snapshot,
                        ):
                            pass
            finally:
                shutil.rmtree(staging_dir, ignore_errors=False)

            error = raised.exception
            self.assertIsNone(error.__cause__)
            self.assertIsNone(error.__context__)
            rendered = "\n".join(
                (
                    str(error),
                    repr(error),
                    repr(error.__cause__),
                    repr(error.__context__),
                    repr(getattr(error, "__notes__", ())),
                )
            )
            self.assertNotIn(secret, rendered)

    def test_staged_audio_setup_baseexception_survives_cleanup_failure(self) -> None:
        secret = "/secret/setup-cleanup"
        for exception_type in (KeyboardInterrupt, SystemExit):
            with self.subTest(exception_type=exception_type.__name__), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                audio = root / "sample.wav"
                audio.write_bytes(b"audio")
                snapshot = transcriber_module._snapshot_private_file(
                    audio,
                    field_name="audio file for backend",
                    include_hash=True,
                )
                staging_dir = root / "staging"
                primary = exception_type("setup interrupted")
                real_rmdir = Path.rmdir

                def make_staging_dir(*_args: object, **_kwargs: object) -> str:
                    staging_dir.mkdir()
                    return str(staging_dir)

                def fail_staging_rmdir(path: Path) -> None:
                    if path == staging_dir:
                        raise OSError(secret)
                    real_rmdir(path)

                with (
                    mock.patch(
                        "speed_of_cinnamon.transcriber.tempfile.mkdtemp",
                        side_effect=make_staging_dir,
                    ),
                    mock.patch(
                        "speed_of_cinnamon.transcriber.open_directory_without_following_symlinks",
                        side_effect=primary,
                    ),
                    mock.patch.object(Path, "rmdir", autospec=True, side_effect=fail_staging_rmdir),
                ):
                    with self.assertRaises(exception_type) as raised:
                        with transcriber_module._staged_audio_file_for_local_backend(
                            audio,
                            expected_snapshot=snapshot,
                        ):
                            self.fail("staging setup unexpectedly succeeded")

                if staging_dir.exists():
                    real_rmdir(staging_dir)

                self.assertIs(raised.exception, primary)
                self.assertIsNone(raised.exception.__cause__)
                self.assertIsNone(raised.exception.__context__)
                rendered = "\n".join(
                    (
                        str(raised.exception),
                        repr(raised.exception),
                        repr(raised.exception.__cause__),
                        repr(raised.exception.__context__),
                        repr(getattr(raised.exception, "__notes__", ())),
                    )
                )
                self.assertNotIn(secret, rendered)

    def test_staged_audio_setup_primary_survives_close_baseexception(self) -> None:
        class CloseFailure(BaseException):
            pass

        secret = "/secret/close"
        for exception_type in (ValueError, KeyboardInterrupt, SystemExit):
            for close_error_type in (
                OSError,
                ValueError,
                KeyboardInterrupt,
                SystemExit,
                CloseFailure,
            ):
                with self.subTest(
                    exception_type=exception_type.__name__,
                    close_error_type=close_error_type.__name__,
                ), tempfile.TemporaryDirectory() as tmp:
                    audio = Path(tmp) / "sample.wav"
                    audio.write_bytes(b"audio")
                    snapshot = transcriber_module._snapshot_private_file(
                        audio,
                        field_name="audio file for backend",
                        include_hash=True,
                    )
                    primary = exception_type("setup primary")
                    close_error = close_error_type(secret)
                    parent_fd = transcriber_module.open_directory_without_following_symlinks(
                        audio.parent,
                        field_name="audio file directory",
                    )
                    real_open = transcriber_module.os.open
                    real_close = transcriber_module.os.close

                    def fail_source_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
                        if kwargs.get("dir_fd") == parent_fd:
                            raise primary
                        return real_open(path, flags, *args, **kwargs)

                    def fail_parent_close(fd: int) -> None:
                        if fd == parent_fd:
                            raise close_error
                        real_close(fd)

                    try:
                        with (
                            mock.patch(
                                "speed_of_cinnamon.transcriber.open_directory_without_following_symlinks",
                                return_value=parent_fd,
                            ),
                            mock.patch("speed_of_cinnamon.transcriber.os.open", side_effect=fail_source_open),
                            mock.patch("speed_of_cinnamon.transcriber.os.close", side_effect=fail_parent_close),
                        ):
                            with self.assertRaises(exception_type) as raised:
                                with transcriber_module._staged_audio_file_for_local_backend(
                                    audio,
                                    expected_snapshot=snapshot,
                                ):
                                    self.fail("staging setup unexpectedly succeeded")
                    finally:
                        try:
                            real_close(parent_fd)
                        except OSError:
                            pass

                    self.assertIs(raised.exception, primary)
                    self.assertIsNone(raised.exception.__cause__)
                    self.assertIsNone(raised.exception.__context__)
                    self.assertIn("staged audio close failed", getattr(raised.exception, "__notes__", ()))
                    rendered = "\n".join(
                        (
                            str(raised.exception),
                            repr(raised.exception),
                            repr(raised.exception.__cause__),
                            repr(raised.exception.__context__),
                            repr(getattr(raised.exception, "__notes__", ())),
                        )
                    )
                    self.assertNotIn(secret, rendered)

    def test_staged_audio_close_without_primary_is_sanitized(self) -> None:
        class CloseFailure(BaseException):
            pass

        secret = "/secret/close-without-primary"
        for close_error_type in (
            OSError,
            ValueError,
            KeyboardInterrupt,
            SystemExit,
            CloseFailure,
        ):
            with self.subTest(close_error_type=close_error_type.__name__), tempfile.TemporaryDirectory() as tmp:
                audio = Path(tmp) / "sample.wav"
                audio.write_bytes(b"audio")
                snapshot = transcriber_module._snapshot_private_file(
                    audio,
                    field_name="audio file for backend",
                    include_hash=True,
                )
                parent_fd = transcriber_module.open_directory_without_following_symlinks(
                    audio.parent,
                    field_name="audio file directory",
                )
                real_close = transcriber_module.os.close

                def fail_parent_close(fd: int) -> None:
                    if fd == parent_fd:
                        raise close_error_type(secret)
                    real_close(fd)

                try:
                    with (
                        mock.patch(
                            "speed_of_cinnamon.transcriber.open_directory_without_following_symlinks",
                            return_value=parent_fd,
                        ),
                        mock.patch("speed_of_cinnamon.transcriber.os.close", side_effect=fail_parent_close),
                    ):
                        expected_type = (
                            close_error_type
                            if close_error_type in (KeyboardInterrupt, SystemExit)
                            else TranscriptionError
                        )
                        with self.assertRaises(expected_type) as raised:
                            with transcriber_module._staged_audio_file_for_local_backend(
                                audio,
                                expected_snapshot=snapshot,
                            ):
                                pass
                finally:
                    try:
                        real_close(parent_fd)
                    except OSError:
                        pass

                error = raised.exception
                self.assertIsNone(error.__cause__)
                self.assertIsNone(error.__context__)
                if expected_type is TranscriptionError:
                    self.assertRegex(str(error), "failed to close staged audio file")
                else:
                    self.assertEqual(str(error), "transcription cleanup interrupted")
                self.assertTrue(getattr(error, "__notes__", ()))
                rendered = "\n".join(
                    (
                        str(error),
                        repr(error),
                        repr(error.__cause__),
                        repr(error.__context__),
                        repr(getattr(error, "__notes__", ())),
                    )
                )
                self.assertNotIn(secret, rendered)

    def test_parent_directory_fd_release_is_exactly_once_and_sanitized(self) -> None:
        secret = "/secret/parent-close"
        for error_type in (OSError, ValueError, KeyboardInterrupt, SystemExit):
            with self.subTest(error_type=error_type.__name__), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                audio = root / "sample.wav"
                audio.write_bytes(b"audio")
                text = root / "result.txt"
                model = root / "model.bin"
                model.write_bytes(b"model")
                real_close = transcriber_module.os.close
                call_builders = (
                    lambda: transcribe_with_template(
                        "printf ok --output-dir {output_dir}",
                        audio,
                        "en",
                        text,
                    ),
                    lambda: transcribe_with_openai_whisper(audio, "en", text),
                    lambda: transcribe_with_whisper_cpp(audio, "en", text, str(root / "model.bin")),
                    lambda: transcribe(audio, "en", text, "printf ok", backend="command"),
                )
                for index, call in enumerate(call_builders):
                    with self.subTest(path=index):
                        parent_fd = transcriber_module.open_directory_without_following_symlinks(
                            root,
                            field_name="test directory",
                        )
                        def fd_fingerprint(fd: int) -> tuple[int, int, int, int, int]:
                            current = transcriber_module.os.fstat(fd)
                            return (
                                current.st_dev,
                                current.st_ino,
                                stat_module.S_IFMT(current.st_mode),
                                current.st_mode & 0o7777,
                                current.st_uid,
                            )

                        original_fingerprint = fd_fingerprint(parent_fd)
                        close_calls: list[int] = []
                        reused_fd_calls: list[int] = []
                        attempted_parent_close_fds: list[int] = []
                        injected_close = False

                        def fail_parent_close(fd: int) -> None:
                            nonlocal injected_close
                            if fd != parent_fd:
                                real_close(fd)
                                return
                            attempted_parent_close_fds.append(fd)
                            try:
                                current_fingerprint = fd_fingerprint(fd)
                            except OSError as exc:
                                raise AssertionError("parent directory fd was closed more than once") from exc
                            if current_fingerprint == original_fingerprint and not injected_close:
                                close_calls.append(fd)
                                injected_close = True
                                real_close(fd)
                                raise error_type(secret)
                            reused_fd_calls.append(fd)

                        try:
                            with (
                                mock.patch(
                                    "speed_of_cinnamon.transcriber.ensure_directory_without_following_symlinks",
                                    return_value=parent_fd,
                                ),
                                mock.patch(
                                    "speed_of_cinnamon.transcriber._command_path",
                                    return_value="/bin/true",
                                ),
                                mock.patch(
                                    "speed_of_cinnamon.transcriber.os.close",
                                    side_effect=fail_parent_close,
                                ),
                            ):
                                expected_type = (
                                    error_type
                                    if error_type in (KeyboardInterrupt, SystemExit)
                                    else TranscriptionError
                                )
                                with self.assertRaises(expected_type) as raised:
                                    call()

                            self.assertEqual(close_calls, [parent_fd])
                            self.assertEqual(attempted_parent_close_fds, [parent_fd])
                            rendered = "\n".join(
                                (
                                    str(raised.exception),
                                    repr(raised.exception),
                                    repr(raised.exception.__cause__),
                                    repr(raised.exception.__context__),
                                    repr(getattr(raised.exception, "__notes__", ())),
                                )
                            )
                            self.assertIsNone(raised.exception.__cause__)
                            self.assertIsNone(raised.exception.__context__)
                            if expected_type is TranscriptionError:
                                self.assertRegex(str(raised.exception), "failed to release transcript directory")
                            else:
                                self.assertEqual(str(raised.exception), "transcription cleanup interrupted")
                            self.assertNotIn(secret, rendered)

                            replacement_path = root / f"replacement-{index}"
                            replacement_path.write_bytes(b"replacement")
                            replacement_source_fd = os.open(replacement_path, os.O_RDONLY)
                            replacement_fingerprint: tuple[int, int, int, int, int] | None = None
                            try:
                                if replacement_source_fd != parent_fd:
                                    os.dup2(replacement_source_fd, parent_fd)
                                    real_close(replacement_source_fd)
                                replacement_fingerprint = fd_fingerprint(parent_fd)
                                self.assertNotEqual(replacement_fingerprint, original_fingerprint)
                                fail_parent_close(parent_fd)
                                self.assertEqual(reused_fd_calls, [parent_fd])
                                self.assertEqual(attempted_parent_close_fds, [parent_fd, parent_fd])
                                self.assertEqual(fd_fingerprint(parent_fd), replacement_fingerprint)
                            finally:
                                try:
                                    current_fingerprint = fd_fingerprint(parent_fd)
                                except OSError:
                                    current_fingerprint = None
                                if (
                                    current_fingerprint is not None
                                    and replacement_fingerprint is not None
                                    and current_fingerprint == replacement_fingerprint
                                ):
                                    real_close(parent_fd)
                        finally:
                            if not injected_close and not reused_fd_calls:
                                try:
                                    if fd_fingerprint(parent_fd) == original_fingerprint:
                                        real_close(parent_fd)
                                except OSError:
                                    pass

    def test_staged_audio_success_releases_temporary_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            snapshot = transcriber_module._snapshot_private_file(
                audio,
                field_name="audio file for backend",
                include_hash=True,
            )
            staged_path: Path | None = None
            with transcriber_module._staged_audio_file_for_local_backend(
                audio,
                expected_snapshot=snapshot,
            ) as staged:
                staged_path = staged
                self.assertEqual(staged.read_bytes(), b"audio")

            self.assertIsNotNone(staged_path)
            self.assertFalse(staged_path.exists())

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
            staged_audio = Path(command[-1])
            staged_audio.with_name(staged_audio.name + ".txt").write_text(
                "hallo cinnamon\n",
                encoding="utf-8",
            )
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

    def test_whisper_cpp_cleans_staged_pwcpp_sidecar_and_ignores_legacy_sidecar(self) -> None:
        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            staged_audio = Path(command[-1])
            staged_audio.with_name(staged_audio.name + ".txt").write_text("staged transcript\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            legacy_generated = Path(tmp) / "sample.wav.txt"
            legacy_generated.write_text("legacy transcript\n", encoding="utf-8")
            text = Path(tmp) / "sample.txt"
            model = Path(tmp) / "ggml-base.bin"
            model.write_bytes(b"model")
            with (
                mock.patch("speed_of_cinnamon.transcriber.resolve_whisper_cpp_command", return_value="pwcpp"),
                mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/pwcpp"),
                mock.patch("speed_of_cinnamon.transcriber._run_transcriber_process", side_effect=fake_run),
            ):
                result = transcribe_with_whisper_cpp(audio, "de", text, str(model))

            self.assertEqual(result, "staged transcript")
            self.assertEqual(text.read_text(encoding="utf-8").strip(), "staged transcript")
            self.assertEqual(legacy_generated.read_text(encoding="utf-8"), "legacy transcript\n")

    def test_whisper_cpp_attempts_all_sidecar_cleanups_after_first_failure(self) -> None:
        staged_sidecar: Path | None = None

        def fake_run(command: list[str], **kwargs: object) -> None:
            nonlocal staged_sidecar
            staged_audio = Path(command[-1])
            staged_sidecar = staged_audio.with_name(staged_audio.name + ".txt")
            staged_sidecar.write_text("staged transcript\n", encoding="utf-8")
            raise CommandChainError("backend failed")

        real_remove = transcriber_module._remove_generated_transcript_file
        remove_calls: list[Path] = []

        def remove_with_first_failure(
            path: Path,
            *,
            field_name: str = "generated transcript",
            expected_target: ExpectedTarget,
        ) -> None:
            remove_calls.append(path)
            if len(remove_calls) == 1:
                raise TranscriptionError("first cleanup failed")
            real_remove(path, field_name=field_name, expected_target=expected_target)

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            legacy_generated = Path(tmp) / "sample.wav.txt"
            legacy_generated.write_text("legacy transcript\n", encoding="utf-8")
            text = Path(tmp) / "sample.txt"
            model = Path(tmp) / "ggml-base.bin"
            model.write_bytes(b"model")
            with (
                mock.patch("speed_of_cinnamon.transcriber.resolve_whisper_cpp_command", return_value="pwcpp"),
                mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/pwcpp"),
                mock.patch("speed_of_cinnamon.transcriber._run_limited_process", side_effect=fake_run),
                mock.patch(
                    "speed_of_cinnamon.transcriber._remove_generated_transcript_file",
                    side_effect=remove_with_first_failure,
                ),
            ):
                with self.assertRaisesRegex(CommandChainError, "backend failed") as raised:
                    transcribe_with_whisper_cpp(audio, "de", text, str(model))

            self.assertIsNotNone(staged_sidecar)
            self.assertEqual(remove_calls[:2], [staged_sidecar, staged_sidecar])
            self.assertNotIn(legacy_generated, remove_calls)
            self.assertTrue(legacy_generated.exists())
            self.assertNotIn("transcript cleanup failed", getattr(raised.exception, "__notes__", []))

    def test_whisper_cpp_reports_capture_failure_when_no_output_exists(self) -> None:
        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        def fail_capture(path: Path, *, field_name: str) -> ExpectedTarget:
            raise RuntimeError("/secret/no-output-capture")

        real_capture = transcriber_module._capture_expected_target
        real_restore = transcriber_module._restore_or_remove_generated_transcript

        def restore_after_capture_failure(
            path: Path,
            snapshot: bytes | None,
            *,
            expected_target: ExpectedTarget,
        ) -> None:
            if expected_target.kind is ExpectedTargetKind.UNKNOWN:
                expected_target = real_capture(path, field_name="generated transcript")
            real_restore(path, snapshot, expected_target=expected_target)

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            model = Path(tmp) / "ggml-base.bin"
            model.write_bytes(b"model")
            with (
                mock.patch("speed_of_cinnamon.transcriber.resolve_whisper_cpp_command", return_value="whisper-cli"),
                mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper-cli"),
                mock.patch("speed_of_cinnamon.transcriber._run_limited_process", side_effect=fake_run),
                mock.patch(
                    "speed_of_cinnamon.transcriber._capture_expected_target",
                    side_effect=fail_capture,
                ),
                mock.patch(
                    "speed_of_cinnamon.transcriber._restore_or_remove_generated_transcript",
                    side_effect=restore_after_capture_failure,
                ),
            ):
                with self.assertRaisesRegex(TranscriptionCleanupError, "failed to clean up generated transcript"):
                    transcribe_with_whisper_cpp(audio, "en", text, str(model))

    def test_whisper_cpp_ignores_dangling_legacy_original_audio_sidecar(self) -> None:
        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            staged_audio = Path(command[-1])
            staged_audio.with_name(staged_audio.name + ".txt").write_text("staged transcript\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            legacy_sidecar = Path(tmp) / "sample.wav.txt"
            legacy_sidecar.symlink_to(Path(tmp) / "missing.txt")
            text = Path(tmp) / "sample.txt"
            model = Path(tmp) / "ggml-base.bin"
            model.write_bytes(b"model")
            with (
                mock.patch("speed_of_cinnamon.transcriber.resolve_whisper_cpp_command", return_value="pwcpp"),
                mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/pwcpp"),
                mock.patch("speed_of_cinnamon.transcriber._run_limited_process", side_effect=fake_run) as mocked_run,
            ):
                result = transcribe_with_whisper_cpp(audio, "de", text, str(model))

            self.assertEqual(result, "staged transcript")
            mocked_run.assert_called_once()
            self.assertTrue(legacy_sidecar.is_symlink())

    def test_whisper_cpp_rejects_dangling_generated_output_after_backend(self) -> None:
        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            text.symlink_to(Path(tmp) / "missing.txt")
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
                with self.assertRaisesRegex(TranscriptionError, "must not pass through a symlink"):
                    transcribe_with_whisper_cpp(audio, "de", text, str(model))

            self.assertTrue(text.is_symlink())

    def test_whisper_cpp_rejects_unchanged_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            text.write_text("stale cpp\n", encoding="utf-8")
            model = Path(tmp) / "ggml-base.bin"
            model.write_bytes(b"model")
            with (
                mock.patch("speed_of_cinnamon.transcriber.resolve_whisper_cpp_command", return_value="whisper-cli"),
                mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper-cli"),
                mock.patch(
                    "speed_of_cinnamon.transcriber._run_transcriber_process",
                    return_value=subprocess.CompletedProcess([], 0, stdout=b"", stderr=b""),
                ),
            ):
                with self.assertRaisesRegex(TranscriptionError, "did not produce a transcript"):
                    transcribe_with_whisper_cpp(audio, "en", text, str(model))
            self.assertEqual(text.read_text(encoding="utf-8"), "stale cpp\n")

    def test_whisper_cpp_restores_existing_output_when_process_removes_it(self) -> None:
        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            text.unlink()
            raise CommandChainError("backend failed")

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            text.write_text("previous cpp\n", encoding="utf-8")
            model = Path(tmp) / "ggml-base.bin"
            model.write_bytes(b"model")
            with (
                mock.patch("speed_of_cinnamon.transcriber.resolve_whisper_cpp_command", return_value="whisper-cli"),
                mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper-cli"),
                mock.patch("speed_of_cinnamon.transcriber._run_transcriber_process", side_effect=fake_run),
            ):
                with self.assertRaisesRegex(TranscriptionError, "transcriber command failed: \\[redacted command error\\]"):
                    transcribe_with_whisper_cpp(audio, "en", text, str(model))
            self.assertEqual(text.read_text(encoding="utf-8"), "previous cpp\n")

    def test_whisper_cpp_restores_existing_output_when_success_removes_it(self) -> None:
        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            text.unlink()
            return subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            text.write_text("previous cpp\n", encoding="utf-8")
            model = Path(tmp) / "ggml-base.bin"
            model.write_bytes(b"model")
            with (
                mock.patch("speed_of_cinnamon.transcriber.resolve_whisper_cpp_command", return_value="whisper-cli"),
                mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper-cli"),
                mock.patch("speed_of_cinnamon.transcriber._run_limited_process", side_effect=fake_run),
            ):
                with self.assertRaisesRegex(TranscriptionError, "did not produce a transcript"):
                    transcribe_with_whisper_cpp(audio, "en", text, str(model))
            self.assertEqual(text.read_text(encoding="utf-8"), "previous cpp\n")

    def test_whisper_cpp_leaves_existing_legacy_sidecar_untouched_after_writing(self) -> None:
        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            staged_audio = Path(command[-1])
            staged_audio.with_name(staged_audio.name + ".txt").write_text(
                "hallo cinnamon\n",
                encoding="utf-8",
            )
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
                    "failed to clean up generated transcript",
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

    def test_whisper_cpp_returns_trusted_stat_after_atomic_restore(self) -> None:
        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            replacement = text.with_name("foreign-output.txt")
            text.unlink()
            replacement.write_text("hallo cinnamon\n", encoding="utf-8")
            replacement.replace(text)
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            text.write_text("previous cpp\n", encoding="utf-8")
            model = Path(tmp) / "ggml-base.bin"
            model.write_bytes(b"model")
            with (
                mock.patch("speed_of_cinnamon.transcriber.resolve_whisper_cpp_command", return_value="whisper-cli"),
                mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper-cli"),
                mock.patch("speed_of_cinnamon.transcriber._run_transcriber_process", side_effect=fake_run),
            ):
                result = transcribe_with_whisper_cpp(audio, "en", text, str(model), write_transcript=False)

            self.assertEqual(result, "hallo cinnamon")
            self.assertEqual(text.read_text(encoding="utf-8"), "previous cpp\n")
            self.assertEqual(result.output_path, text)
            self.assertIsNotNone(result.output_stat)
            self.assertEqual(result.output_stat.st_ino, text.stat().st_ino)
            self.assertEqual(result.output_stat.st_nlink, 1)

    def test_whisper_cpp_rejects_unsafe_existing_output_replacements(self) -> None:
        cases = ("regular", "symlink", "hardlink")
        for mode in cases:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                audio = root / "sample.wav"
                audio.write_bytes(b"audio")
                text = root / "sample.txt"
                text.write_text("previous cpp\n", encoding="utf-8")
                foreign = root / "foreign.txt"
                foreign.write_text("foreign output\n", encoding="utf-8")
                model = root / "ggml-base.bin"
                model.write_bytes(b"model")

                def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
                    text.unlink()
                    if mode == "regular":
                        text.write_text("foreign output\n", encoding="utf-8")
                    elif mode == "symlink":
                        text.symlink_to(foreign)
                    else:
                        os.link(foreign, text)
                    return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

                with (
                    mock.patch("speed_of_cinnamon.transcriber.resolve_whisper_cpp_command", return_value="whisper-cli"),
                    mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper-cli"),
                    mock.patch("speed_of_cinnamon.transcriber._run_transcriber_process", side_effect=fake_run),
                ):
                    if mode == "regular":
                        result = transcribe_with_whisper_cpp(
                            audio,
                            "en",
                            text,
                            str(model),
                            write_transcript=False,
                        )
                        self.assertEqual(result, "foreign output")
                        self.assertEqual(result.output_path, text)
                        self.assertIsNotNone(result.output_stat)
                        self.assertEqual(result.output_stat.st_nlink, 1)
                    else:
                        with self.assertRaises(TranscriptionError) as raised:
                            transcribe_with_whisper_cpp(
                                audio,
                                "en",
                                text,
                                str(model),
                                write_transcript=False,
                            )
                        visible_exception_data = "\n".join(
                            [
                                str(raised.exception),
                                repr(raised.exception),
                                str(raised.exception.__cause__),
                                str(raised.exception.__context__),
                                *getattr(raised.exception, "__notes__", ()),
                            ]
                        )
                        self.assertNotIn(str(text), visible_exception_data)
                        self.assertNotIn(str(foreign), visible_exception_data)

                self.assertTrue(foreign.exists())
                self.assertEqual(foreign.read_text(encoding="utf-8"), "foreign output\n")
                if mode == "regular":
                    self.assertEqual(text.read_text(encoding="utf-8"), "previous cpp\n")
                elif mode == "symlink":
                    self.assertTrue(text.is_symlink())
                    self.assertEqual(os.readlink(text), str(foreign))
                else:
                    self.assertEqual(text.stat().st_ino, foreign.stat().st_ino)

    def test_whisper_cpp_does_not_remove_pwcpp_sidecar_replacement_after_fd_capture(self) -> None:
        real_remove = transcriber_module._remove_generated_transcript_file
        replaced_path: list[Path] = []

        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            staged_audio = Path(command[-1])
            staged_audio.with_name(staged_audio.name + ".txt").write_text(
                "backend transcript\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        def replace_then_remove(
            path: Path,
            *,
            field_name: str = "generated transcript",
            expected_target: ExpectedTarget,
        ) -> None:
            if path.name.endswith(".wav.txt") and not replaced_path:
                foreign = path.parent.parent / "foreign-sidecar.txt"
                foreign.write_text("foreign replacement\n", encoding="utf-8")
                path.unlink()
                foreign.replace(path)
                replaced_path.append(path)
            real_remove(path, field_name=field_name, expected_target=expected_target)

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            model = Path(tmp) / "ggml-base.bin"
            model.write_bytes(b"model")
            with (
                mock.patch("speed_of_cinnamon.transcriber.resolve_whisper_cpp_command", return_value="pwcpp"),
                mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/pwcpp"),
                mock.patch("speed_of_cinnamon.transcriber._run_limited_process", side_effect=fake_run),
                mock.patch(
                    "speed_of_cinnamon.transcriber._remove_generated_transcript_file",
                    side_effect=replace_then_remove,
                ),
            ):
                with self.assertRaisesRegex(TranscriptionError, "failed to clean up generated transcript"):
                    transcribe_with_whisper_cpp(audio, "de", text, str(model))

            self.assertEqual(len(replaced_path), 1)
            self.assertEqual(
                replaced_path[0].read_text(encoding="utf-8"),
                "foreign replacement\n",
            )

    def test_openai_whisper_preserves_primary_exception_when_cleanup_fails(self) -> None:
        primary = RuntimeError("primary backend failure")

        def fake_run(command: list[str], **kwargs: object) -> None:
            text.write_text("transient transcript\n", encoding="utf-8")
            raise primary

        real_remove = transcriber_module._remove_generated_transcript_file

        def fail_transcript_cleanup(
            path: Path,
            *,
            field_name: str = "generated transcript",
            expected_target: ExpectedTarget,
        ) -> None:
            if path == text:
                raise TranscriptionError("cleanup failure")
            real_remove(path, field_name=field_name, expected_target=expected_target)

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            with (
                mock.patch("speed_of_cinnamon.transcriber._command_path", return_value="/usr/bin/whisper"),
                mock.patch("speed_of_cinnamon.transcriber._run_limited_process", side_effect=fake_run),
                mock.patch(
                    "speed_of_cinnamon.transcriber._remove_generated_transcript_file",
                    side_effect=fail_transcript_cleanup,
                ),
            ):
                with self.assertRaises(RuntimeError) as raised:
                    transcribe_with_openai_whisper(audio, "en", text)

        self.assertIs(raised.exception, primary)
        self.assertTrue(
            any("transcript cleanup failed" in note for note in getattr(primary, "__notes__", []))
        )

    def test_openai_whisper_snapshot_state_is_bound_to_same_file(self) -> None:
        real_fdopen = os.fdopen
        mutated = False

        def mutate_before_read(fd: int, mode: str):
            nonlocal mutated
            if not mutated:
                mutated = True
                text.write_bytes(b"replacement transcript with a different size\n")
            return real_fdopen(fd, mode)

        with tempfile.TemporaryDirectory() as tmp:
            text = Path(tmp) / "sample.txt"
            text.write_bytes(b"original transcript\n")
            with mock.patch("speed_of_cinnamon.transcriber.os.fdopen", side_effect=mutate_before_read):
                with self.assertRaisesRegex(TranscriptionError, "changed while snapshotting"):
                    transcriber_module._snapshot_existing_file(text)

        self.assertTrue(mutated)

    def test_staged_audio_cleanup_base_exception_preserves_body_exception(self) -> None:
        class CleanupInterrupt(BaseException):
            pass

        primary = SystemExit("body failure")
        cleanup_interrupt = CleanupInterrupt("cleanup failure")
        captured_error: BaseException | None = None
        rmdir_calls: list[Path] = []
        real_rmdir = Path.rmdir

        def tracking_rmdir(path: Path) -> None:
            rmdir_calls.append(path)
            real_rmdir(path)

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            snapshot = transcriber_module._snapshot_private_file(
                audio,
                field_name="audio file for backend",
                include_hash=True,
            )
            with (
                mock.patch(
                    "speed_of_cinnamon.transcriber._remove_generated_transcript_file",
                    side_effect=cleanup_interrupt,
                ),
                mock.patch.object(Path, "rmdir", autospec=True, side_effect=tracking_rmdir),
            ):
                try:
                    with transcriber_module._staged_audio_file_for_local_backend(
                        audio,
                        expected_snapshot=snapshot,
                    ):
                        raise primary
                except BaseException as exc:
                    captured_error = exc

        self.assertIs(captured_error, primary)
        self.assertEqual(len(rmdir_calls), 1)
        self.assertTrue(
            any("failed to clean up staged audio file" in note for note in getattr(primary, "__notes__", []))
        )

    def test_staged_audio_cleanup_preserves_primary_when_add_note_fails(self) -> None:
        class NoteFailingPrimary(RuntimeError):
            def add_note(self, note: str) -> None:
                raise KeyboardInterrupt("note append failed")

        primary = NoteFailingPrimary("body failure")
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            snapshot = transcriber_module._snapshot_private_file(
                audio,
                field_name="audio file for backend",
                include_hash=True,
            )
            with (
                mock.patch(
                    "speed_of_cinnamon.transcriber._remove_generated_transcript_file",
                    side_effect=TranscriptionError("cleanup failure"),
                ),
                self.assertRaises(NoteFailingPrimary) as raised,
            ):
                with transcriber_module._staged_audio_file_for_local_backend(
                    audio,
                    expected_snapshot=snapshot,
                ):
                    raise primary

        self.assertIs(raised.exception, primary)

    def test_template_restore_retries_when_output_stat_capture_fails(self) -> None:
        def fake_run(*args: object, **kwargs: object) -> str:
            text.unlink()
            return "backend output"

        real_capture_expected_target = transcriber_module._capture_expected_target
        transcript_inspections = 0

        def fail_first_capture(path: Path, *, field_name: str) -> ExpectedTarget:
            nonlocal transcript_inspections
            if path == text:
                transcript_inspections += 1
                if transcript_inspections == 1:
                    return ExpectedTarget.unknown()
            return real_capture_expected_target(path, field_name=field_name)

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            text.write_bytes(b"original transcript\n")
            with (
                mock.patch("speed_of_cinnamon.transcriber.run_command_chain", side_effect=fake_run),
                mock.patch(
                    "speed_of_cinnamon.transcriber._capture_expected_target",
                    side_effect=fail_first_capture,
                ),
            ):
                result = transcribe_with_template("printf --output {text}", audio, "en", text)

            self.assertEqual(result, "backend output")
            self.assertEqual(text.read_bytes(), b"original transcript\n")
            self.assertGreaterEqual(transcript_inspections, 2)

    def test_read_text_file_wraps_symlink_ancestor_runtime_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            text = Path(tmp) / "sample.txt"
            text.write_text("transcript\n", encoding="utf-8")
            with mock.patch(
                "speed_of_cinnamon.transcriber.assert_no_symlink_ancestors",
                side_effect=RuntimeError("unsafe ancestor"),
            ):
                with self.assertRaisesRegex(TranscriptionError, "failed to read generated transcript") as raised:
                    transcriber_module._read_text_file(text)

        self.assertIsInstance(raised.exception.__cause__, RuntimeError)

    def test_restore_existing_snapshot_does_not_modify_hardlink_created_after_identity_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            text = Path(tmp) / "sample.txt"
            text.write_bytes(b"current inode contents\n")
            expected_target = _capture_expected_target_for_test(text)
            foreign_hardlink = Path(tmp) / "foreign-hardlink.txt"
            os.link(text, foreign_hardlink)

            with self.assertRaises(TranscriptionError):
                transcriber_module._restore_existing_file_snapshot(
                    text,
                    b"restored snapshot\n",
                    expected_target=expected_target,
                )

            self.assertEqual(foreign_hardlink.read_bytes(), b"current inode contents\n")

    def test_whisper_cpp_cleans_generated_output_before_keyboard_interrupt(self) -> None:
        def fake_run(command: list[str], **kwargs: object) -> None:
            text.write_text("backend transcript\n", encoding="utf-8")
            raise KeyboardInterrupt("/secret/backend-interrupt")

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            model = Path(tmp) / "ggml-base.bin"
            model.write_bytes(b"model")
            with (
                mock.patch("speed_of_cinnamon.transcriber.resolve_whisper_cpp_command", return_value="whisper-cli"),
                mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper-cli"),
                mock.patch("speed_of_cinnamon.transcriber._run_limited_process", side_effect=fake_run),
            ):
                with self.assertRaises(KeyboardInterrupt) as raised:
                    transcribe_with_whisper_cpp(audio, "en", text, str(model))

            self.assertFalse(text.exists())
            self.assertEqual(str(raised.exception), "/secret/backend-interrupt")

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

    def test_invalid_scalar_options_create_no_transcript_directory(self) -> None:
        cases = (
            ("command type", {"command_template": None}, "command template must be text"),
            ("command length", {"command_template": "x" * (MAX_TRANSCRIBER_TEXT_CHARS + 1)}, "command template is too large"),
            ("backend type", {"backend": None}, "backend must be text"),
            ("backend value", {"backend": "not-a-backend"}, "unknown transcriber backend"),
            ("model type", {"whisper_model": None}, "whisper model must be text"),
            ("model control", {"whisper_model": "model\x85.bin"}, "whisper model contains invalid control character"),
            ("context type", {"personal_context": None}, "personal context must be text"),
            (
                "context length",
                {"personal_context": "x" * (MAX_PERSONAL_CONTEXT_CHARS + 1)},
                "personal context is too large",
            ),
            ("vocabulary type", {"vocabulary": None}, "vocabulary must be text"),
            (
                "vocabulary length",
                {"vocabulary": "x" * (MAX_VOCABULARY_CHARS + 1)},
                "vocabulary is too large",
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "sample.wav"
            audio.write_bytes(b"audio")
            for index, (label, options, message) in enumerate(cases):
                with self.subTest(label=label):
                    text = root / f"case-{index}" / "result.txt"
                    with self.assertRaisesRegex(TranscriptionError, message):
                        transcribe(audio, "en", text, **options)
                    self.assertFalse(text.parent.exists())

    def test_invalid_audio_or_openai_options_create_no_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "sample.wav"
            audio.write_bytes(b"audio")
            cases = (
                (
                    "missing audio",
                    root / "missing.wav",
                    {"backend": "command", "command_template": "printf ok"},
                ),
                (
                    "unsupported audio",
                    root / "sample.txt",
                    {"backend": "command", "command_template": "printf ok"},
                ),
                (
                    "openai model type",
                    audio,
                    {"backend": "openai-compatible", "openai_compatible_model": None},
                ),
                (
                    "openai model empty",
                    audio,
                    {"backend": "openai-compatible", "openai_compatible_model": ""},
                ),
                (
                    "openai model length",
                    audio,
                    {
                        "backend": "openai-compatible",
                        "openai_compatible_model": "x" * (MAX_OPENAI_COMPATIBLE_MODEL_CHARS + 1),
                    },
                ),
                (
                    "openai url",
                    audio,
                    {
                        "backend": "openai-compatible",
                        "openai_compatible_model": "gpt-4o-transcribe",
                        "openai_compatible_url": "not-a-url",
                    },
                ),
                (
                    "openai api key",
                    audio,
                    {
                        "backend": "openai-compatible",
                        "openai_compatible_model": "gpt-4o-transcribe",
                        "openai_compatible_api_key": "secret\x00",
                    },
                ),
                (
                    "openai flex type",
                    audio,
                    {
                        "backend": "openai-compatible",
                        "openai_compatible_model": "gpt-4o-transcribe",
                        "openai_compatible_flex_processing": "false",
                    },
                ),
                (
                    "openai fallback type",
                    audio,
                    {
                        "backend": "openai-compatible",
                        "openai_compatible_model": "gpt-4o-transcribe",
                        "openai_compatible_service_tier_fallback": 1,
                    },
                ),
                (
                    "openai unsupported model",
                    audio,
                    {
                        "backend": "openai-compatible",
                        "openai_compatible_model": "gpt-5",
                        "openai_compatible_url": "https://api.openai.com/v1",
                    },
                ),
            )
            for index, (label, input_audio, options) in enumerate(cases):
                with self.subTest(label=label):
                    text = root / f"invalid-{index}" / "result.txt"
                    with self.assertRaises(TranscriptionError):
                        transcribe(input_audio, "en", text, **options)  # type: ignore[arg-type]
                    self.assertFalse(text.parent.exists())

    def test_transcribe_rejects_externally_held_output_lock_before_backend(self) -> None:
        lock_name = ".speed-of-cinnamon-transcriber.lock"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "sample.wav"
            audio.write_bytes(b"audio")
            text = root / "result.txt"
            lock_path = root / lock_name
            lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with mock.patch(
                    "speed_of_cinnamon.transcriber.transcribe_with_template",
                    side_effect=AssertionError("backend must not run"),
                ):
                    with self.assertRaisesRegex(TranscriptionError, "transcript output namespace is busy"):
                        transcribe(audio, "en", text, "printf ignored", backend="command")
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)

    def test_transcribe_holds_output_lock_during_backend_and_releases_after_success(self) -> None:
        lock_name = ".speed-of-cinnamon-transcriber.lock"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "sample.wav"
            audio.write_bytes(b"audio")
            text = root / "result.txt"
            lock_path = root / lock_name
            observed_held = False

            def backend_probe(*_args: object, **_kwargs: object) -> str:
                nonlocal observed_held
                probe_fd = os.open(lock_path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
                try:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(probe_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    observed_held = True
                finally:
                    os.close(probe_fd)
                return "hello cinnamon"

            with mock.patch(
                "speed_of_cinnamon.transcriber.transcribe_with_template",
                side_effect=backend_probe,
            ):
                result = transcribe(audio, "en", text, "printf ignored", backend="command")

            self.assertEqual(result, "hello cinnamon")
            self.assertTrue(observed_held)
            lock_stat = lock_path.stat()
            self.assertTrue(stat_module.S_ISREG(lock_stat.st_mode))
            self.assertEqual(lock_stat.st_mode & 0o777, 0o600)
            self.assertEqual(lock_stat.st_nlink, 1)

            probe_fd = os.open(lock_path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                fcntl.flock(probe_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(probe_fd, fcntl.LOCK_UN)
            finally:
                os.close(probe_fd)

    def test_transcribe_releases_output_lock_after_backend_exception(self) -> None:
        lock_name = ".speed-of-cinnamon-transcriber.lock"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "sample.wav"
            audio.write_bytes(b"audio")
            text = root / "result.txt"
            lock_path = root / lock_name
            with (
                mock.patch(
                    "speed_of_cinnamon.transcriber.transcribe_with_template",
                    side_effect=RuntimeError("backend failed"),
                ),
                self.assertRaisesRegex(RuntimeError, "backend failed"),
            ):
                transcribe(audio, "en", text, "printf ignored", backend="command")

            probe_fd = os.open(lock_path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                fcntl.flock(probe_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(probe_fd, fcntl.LOCK_UN)
            finally:
                os.close(probe_fd)

    def test_transcribe_rejects_unprivate_output_namespace_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "output"
            output.mkdir()
            output.chmod(0o755)
            audio = root / "sample.wav"
            audio.write_bytes(b"audio")
            text = output / "result.txt"
            lock_path = output / ".speed-of-cinnamon-transcriber.lock"
            with mock.patch(
                "speed_of_cinnamon.transcriber.transcribe_with_template",
                side_effect=AssertionError("backend must not run"),
            ):
                with self.assertRaisesRegex(TranscriptionError, "failed to prepare transcript output lock"):
                    transcribe(audio, "en", text, "printf ignored", backend="command")
            self.assertFalse(lock_path.exists())

    def test_transcribe_reports_output_lock_unlock_failure_after_success(self) -> None:
        real_flock = fcntl.flock

        def fail_unlock(fd: int, operation: int) -> None:
            if operation == fcntl.LOCK_UN:
                raise OSError("unlock secret")
            real_flock(fd, operation)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "sample.wav"
            audio.write_bytes(b"audio")
            text = root / "result.txt"
            with (
                mock.patch(
                    "speed_of_cinnamon.transcriber.transcribe_with_template",
                    return_value="hello cinnamon",
                ),
                mock.patch("speed_of_cinnamon.transcriber.fcntl.flock", side_effect=fail_unlock),
            ):
                with self.assertRaisesRegex(TranscriptionError, "failed to release transcript output lock"):
                    transcribe(audio, "en", text, "printf ignored", backend="command")

    def test_transcribe_reports_output_lock_close_failure_after_success(self) -> None:
        real_flock = fcntl.flock
        real_close = os.close
        lock_fds: set[int] = set()

        def track_lock(fd: int, operation: int) -> None:
            if operation & fcntl.LOCK_EX:
                lock_fds.add(fd)
            real_flock(fd, operation)

        def fail_lock_close(fd: int) -> None:
            if fd in lock_fds:
                real_close(fd)
                raise OSError("close secret")
            real_close(fd)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "sample.wav"
            audio.write_bytes(b"audio")
            text = root / "result.txt"
            with (
                mock.patch(
                    "speed_of_cinnamon.transcriber.transcribe_with_template",
                    return_value="hello cinnamon",
                ),
                mock.patch("speed_of_cinnamon.transcriber.fcntl.flock", side_effect=track_lock),
                mock.patch("speed_of_cinnamon.transcriber.os.close", side_effect=fail_lock_close),
            ):
                with self.assertRaisesRegex(TranscriptionError, "failed to release transcript output lock"):
                    transcribe(audio, "en", text, "printf ignored", backend="command")

    def test_transcribe_preserves_body_baseexception_when_output_lock_release_fails(self) -> None:
        primary = KeyboardInterrupt("primary secret")
        real_flock = fcntl.flock
        real_close = os.close
        unlock_fds: set[int] = set()
        close_attempts: list[int] = []

        def fail_unlock(fd: int, operation: int) -> None:
            if operation == fcntl.LOCK_UN:
                unlock_fds.add(fd)
                raise OSError("unlock secret")
            real_flock(fd, operation)

        def fail_lock_close(fd: int) -> None:
            if fd in unlock_fds:
                close_attempts.append(fd)
                real_close(fd)
                raise OSError("close secret")
            real_close(fd)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "sample.wav"
            audio.write_bytes(b"audio")
            text = root / "result.txt"
            with (
                mock.patch(
                    "speed_of_cinnamon.transcriber.transcribe_with_template",
                    side_effect=primary,
                ),
                mock.patch("speed_of_cinnamon.transcriber.fcntl.flock", side_effect=fail_unlock),
                mock.patch("speed_of_cinnamon.transcriber.os.close", side_effect=fail_lock_close),
            ):
                with self.assertRaises(KeyboardInterrupt) as raised:
                    transcribe(audio, "en", text, "printf ignored", backend="command")

            self.assertIs(raised.exception, primary)
            self.assertTrue(close_attempts)
            self.assertIn(
                "transcript output lock release failed",
                getattr(primary, "__notes__", []),
            )
            self.assertNotIn("unlock secret", repr(primary))
            self.assertNotIn("close secret", repr(primary))

    def test_transcribe_preserves_primary_when_lock_note_append_fails(self) -> None:
        class NoteFailingPrimary(RuntimeError):
            def add_note(self, note: str) -> None:
                raise KeyboardInterrupt("note append failed")

        primary = NoteFailingPrimary("primary failure")
        real_flock = fcntl.flock
        real_close = os.close
        unlock_fds: set[int] = set()

        def fail_unlock(fd: int, operation: int) -> None:
            if operation == fcntl.LOCK_UN:
                unlock_fds.add(fd)
                raise OSError("unlock failure")
            real_flock(fd, operation)

        def fail_lock_close(fd: int) -> None:
            if fd in unlock_fds:
                real_close(fd)
                raise OSError("close failure")
            real_close(fd)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "sample.wav"
            audio.write_bytes(b"audio")
            text = root / "result.txt"
            with (
                mock.patch(
                    "speed_of_cinnamon.transcriber.transcribe_with_template",
                    side_effect=primary,
                ),
                mock.patch("speed_of_cinnamon.transcriber.fcntl.flock", side_effect=fail_unlock),
                mock.patch("speed_of_cinnamon.transcriber.os.close", side_effect=fail_lock_close),
            ):
                with self.assertRaises(NoteFailingPrimary) as raised:
                    transcribe(audio, "en", text, "printf ignored", backend="command")

        self.assertIs(raised.exception, primary)

    def test_transcribe_rejects_unsafe_existing_output_lock(self) -> None:
        cases = ("symlink", "hardlink", "wrong-mode")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                audio = root / "sample.wav"
                audio.write_bytes(b"audio")
                text = root / "result.txt"
                lock_path = root / ".speed-of-cinnamon-transcriber.lock"
                if case == "symlink":
                    foreign = root / "foreign.lock"
                    foreign.write_bytes(b"foreign")
                    lock_path.symlink_to(foreign)
                elif case == "hardlink":
                    seed = root / "seed.lock"
                    seed.write_bytes(b"seed")
                    os.link(seed, lock_path)
                else:
                    lock_path.write_bytes(b"unsafe mode")
                    lock_path.chmod(0o644)

                with mock.patch(
                    "speed_of_cinnamon.transcriber.transcribe_with_template",
                    side_effect=AssertionError("backend must not run"),
                ):
                    with self.assertRaisesRegex(TranscriptionError, "transcript output lock is unsafe"):
                        transcribe(audio, "en", text, "printf ignored", backend="command")

    def test_transcribe_rejects_trusted_output_for_different_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "sample.wav"
            audio.write_bytes(b"audio")
            text = root / "sample.txt"
            foreign = root / "foreign.txt"
            foreign.write_text("foreign transcript\n", encoding="utf-8")
            forged = transcriber_module._TrustedTranscriptText("hello cinnamon", foreign, foreign.stat())

            with (
                mock.patch("speed_of_cinnamon.transcriber.resolve_transcriber", return_value="command"),
                mock.patch("speed_of_cinnamon.transcriber.transcribe_with_template", return_value=forged),
            ):
                with self.assertRaisesRegex(TranscriptionError, "transcript output is unsafe"):
                    transcribe(audio, "en", text, "printf ignored")

    def test_transcribe_rejects_trusted_output_after_path_identity_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "sample.wav"
            audio.write_bytes(b"audio")
            text = root / "sample.txt"
            text.write_text("old transcript\n", encoding="utf-8")
            old_stat = text.stat()
            replacement = root / "replacement.txt"

            def replace_before_return(*_args: object, **_kwargs: object) -> _TrustedTranscriptText:
                replacement.write_text("foreign transcript\n", encoding="utf-8")
                replacement.replace(text)
                return transcriber_module._TrustedTranscriptText("hello cinnamon", text, old_stat)

            with (
                mock.patch("speed_of_cinnamon.transcriber.resolve_transcriber", return_value="command"),
                mock.patch(
                    "speed_of_cinnamon.transcriber.transcribe_with_template",
                    side_effect=replace_before_return,
                ),
            ):
                with self.assertRaisesRegex(TranscriptionError, "transcript output changed before return"):
                    transcribe(audio, "en", text, "printf ignored")

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

    def test_command_personalization_placeholders_support_multiline_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            cases = (
                ("printf {context}", "line one\nline two", "line one\nline two"),
                ("printf {vocabulary}", "PipeWire\nCinnamon", "PipeWire\nCinnamon"),
            )
            for template, value, expected in cases:
                with self.subTest(template=template):
                    result = transcribe(
                        audio,
                        "en",
                        text,
                        template,
                        personal_context=value if template.endswith("context}") else "",
                        vocabulary=value if template.endswith("vocabulary}") else "",
                    )
                    self.assertEqual(result, expected)

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

    def test_transcribe_rejects_oversized_language_before_local_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            with mock.patch("speed_of_cinnamon.transcriber._run_transcriber_process", side_effect=AssertionError("backend called")) as mocked_run:
                with self.assertRaisesRegex(TranscriptionError, "language is too large"):
                    transcribe(audio, "x" * (transcriber_module.MAX_LANGUAGE_CODE_CHARS + 1), Path(tmp) / "sample.txt", "printf ok")

        mocked_run.assert_not_called()

    def test_transcribe_rejects_language_control_character_before_local_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            with mock.patch("speed_of_cinnamon.transcriber._run_transcriber_process", side_effect=AssertionError("backend called")) as mocked_run:
                with self.assertRaisesRegex(TranscriptionError, "language contains invalid control character"):
                    transcribe(audio, "de\r\nbad", Path(tmp) / "sample.txt", "printf ok")

        mocked_run.assert_not_called()

    def test_transcribe_rejects_empty_language_before_local_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            with mock.patch("speed_of_cinnamon.transcriber._run_transcriber_process", side_effect=AssertionError("backend called")) as mocked_run:
                with self.assertRaisesRegex(TranscriptionError, "language must not be empty"):
                    transcribe(audio, "  ", Path(tmp) / "sample.txt", "printf ok")

        mocked_run.assert_not_called()

    def test_openai_whisper_direct_helper_rejects_oversized_language_before_command_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            text = Path(tmp) / "sample.txt"
            with mock.patch("speed_of_cinnamon.transcriber._command_path", side_effect=AssertionError("command lookup called")) as mocked_command:
                with self.assertRaisesRegex(TranscriptionError, "language is too large"):
                    transcribe_with_openai_whisper(
                        audio,
                        "x" * (transcriber_module.MAX_LANGUAGE_CODE_CHARS + 1),
                        text,
                    )

        mocked_command.assert_not_called()

    def test_whisper_cpp_direct_helper_rejects_language_control_before_model_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            text = Path(tmp) / "sample.txt"
            model = Path(tmp) / "missing.bin"
            with mock.patch("speed_of_cinnamon.transcriber._validate_local_model_path", side_effect=AssertionError("model lookup called")) as mocked_model:
                with self.assertRaisesRegex(TranscriptionError, "language contains invalid control character"):
                    transcribe_with_whisper_cpp(audio, "de\r\nbad", text, str(model))

        mocked_model.assert_not_called()

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
            def __init__(self) -> None:
                self._read = False

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

    def test_openai_compatible_api_rejects_non_text_response_text(self) -> None:
        class Response:
            def __init__(self) -> None:
                self._read = False

            def __enter__(self):
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self, size: int = -1) -> bytes:
                if self._read:
                    return b""
                self._read = True
                return b'{"text":123}'

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            with mock.patch("speed_of_cinnamon.transcriber._open_http_request", return_value=Response()):
                with self.assertRaisesRegex(TranscriptionError, "response text must be text"):
                    transcribe_with_openai_compatible_api(
                        audio,
                        "en",
                        Path(tmp) / "sample.txt",
                        model="local-transcriber",
                        url="http://127.0.0.1:8000/v1",
                    )

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

    def test_openai_compatible_api_redacts_non_text_http_error_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            error = urllib.error.HTTPError(
                "https://api.openai.com/v1/audio/transcriptions",
                400,
                object(),
                {},
                io.BytesIO(b""),
            )
            with mock.patch("speed_of_cinnamon.transcriber._open_http_request", side_effect=error):
                with self.assertRaisesRegex(TranscriptionError, r"OpenAI-compatible speech API failed \(400\)"):
                    transcribe_with_openai_compatible_api(
                        audio,
                        "en",
                        Path(tmp) / "sample.txt",
                        model="gpt-4o-transcribe",
                        url="https://api.openai.com/v1",
                        openai_compatible_service_tier_fallback=True,
                    )

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

    def test_multipart_form_data_rejects_control_characters_in_file_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio_path = Path(tmp) / "sample.wav"
            audio_path.write_bytes(b"audio")
            with self.assertRaisesRegex(TranscriptionError, "multipart file field contains invalid control character"):
                _multipart_form_data({}, "file\r\nX-Injected: yes", audio_path)

    def test_multipart_form_data_escapes_quotes_in_file_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio_path = Path(tmp) / "sample.wav"
            audio_path.write_bytes(b"audio")
            body, _boundary = _multipart_form_data({}, 'file"name', audio_path)

        self.assertIn(b'name="file\\"name"', body)

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

    def test_openai_compatible_endpoint_ignores_suffix_collisions(self) -> None:
        self.assertEqual(
            transcriber_module._openai_compatible_endpoint("http://127.0.0.1:8000/v1x", "/audio/transcriptions"),
            "http://127.0.0.1:8000/v1x/audio/transcriptions",
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
            self.assertNotIn("https://api.openai.com", str(raised.exception))
            self.assertNotIn("/v1/audio/transcriptions", str(raised.exception))

    def test_openai_remote_error_boundaries_are_chain_free_and_redacted(self) -> None:
        secret = "/srv/private/remote-token filename=secret.wav"
        remote_url = "https://api.example.test/v1/remote-token/audio/transcriptions"

        class Response:
            def __init__(self, payload: bytes) -> None:
                self.payload = payload
                self.read_once = False

            def __enter__(self):
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self, size: int = -1) -> bytes:
                if self.read_once:
                    return b""
                self.read_once = True
                return self.payload

        cases = (
            (
                "http",
                urllib.error.HTTPError(
                    remote_url,
                    418,
                    secret,
                    {},
                    io.BytesIO(secret.encode("utf-8")),
                ),
                "OpenAI-compatible speech API failed (418)",
            ),
            (
                "os",
                OSError(secret),
                "OpenAI-compatible speech API is not reachable",
            ),
            (
                "url",
                urllib.error.URLError(secret),
                "OpenAI-compatible speech API is not reachable",
            ),
            (
                "json",
                Response(secret.encode("utf-8")),
                "OpenAI-compatible speech API returned invalid JSON",
            ),
            (
                "unicode",
                Response(b"\xff"),
                "API response is not valid UTF-8",
            ),
            (
                "remote payload",
                Response(f'{{"error":{{"message":"{secret}"}}}}'.encode("utf-8")),
                "OpenAI-compatible speech API failed",
            ),
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "sample.wav"
            audio.write_bytes(b"audio")
            for index, (label, failure, expected_message) in enumerate(cases):
                with self.subTest(label=label):
                    output = root / f"remote-error-{index}" / "result.txt"
                    patch_kwargs = (
                        {"side_effect": failure}
                        if isinstance(failure, BaseException)
                        else {"return_value": failure}
                    )
                    with mock.patch(
                        "speed_of_cinnamon.transcriber._open_http_request",
                        **patch_kwargs,
                    ):
                        with self.assertRaises(TranscriptionError) as raised:
                            transcribe_with_openai_compatible_api(
                                audio,
                                "en",
                                output,
                                model="local-transcriber",
                                url=remote_url,
                                api_key="api-key",
                                write_transcript=False,
                            )

                    error = raised.exception
                    self.assertIn(expected_message, str(error))
                    self.assertIsNone(error.__cause__)
                    self.assertIsNone(error.__context__)
                    rendered = "".join(
                        (
                            str(error),
                            repr(error),
                            repr(error.args),
                            repr(error.__cause__),
                            repr(error.__context__),
                            repr(getattr(error, "__notes__", ())),
                            "".join(traceback.format_exception(error)),
                        )
                    )
                    self.assertNotIn(secret, rendered)
                    self.assertNotIn(remote_url, rendered)
                    self.assertFalse(output.parent.exists())

    def test_read_response_text_maps_bounded_read_errors_and_preserves_control_flow(self) -> None:
        secret = "/srv/private/partial-token"

        class FailingResponse:
            def __init__(self, failure: BaseException) -> None:
                self.failure = failure

            def read(self, size: int = -1) -> bytes:
                raise self.failure

        failures = (
            OSError(secret),
            http.client.IncompleteRead(secret.encode("utf-8")),
            ValueError(secret),
            RuntimeError(secret),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                with self.assertRaisesRegex(TranscriptionError, "API response read failed") as raised:
                    transcriber_module._read_response_text(FailingResponse(failure))
                error = raised.exception
                self.assertIsNone(error.__cause__)
                self.assertIsNone(error.__context__)
                rendered = "".join(
                    (
                        str(error),
                        repr(error),
                        repr(error.args),
                        repr(error.__cause__),
                        repr(error.__context__),
                        repr(getattr(error, "__notes__", ())),
                        "".join(traceback.format_exception(error)),
                    )
                )
                self.assertNotIn(secret, rendered)

        for exception_type in (KeyboardInterrupt, SystemExit, GeneratorExit):
            with self.subTest(failure=exception_type.__name__):
                control_flow = exception_type("/srv/private/control-flow-token")
                with self.assertRaises(exception_type) as raised:
                    transcriber_module._read_response_text(FailingResponse(control_flow))
                self.assertIs(raised.exception, control_flow)

    def test_openai_http_error_body_read_boundaries_preserve_status_fallback_and_json_success(self) -> None:
        secret = "/srv/private/http-body-token"
        url = "https://api.example.test/v1/http-body-token/audio/transcriptions"

        class Body:
            def __init__(self, payload: bytes | None = None, failure: BaseException | None = None) -> None:
                self.payload = payload
                self.failure = failure
                self.read_once = False
                self.closed = False

            def __enter__(self):
                return self

            def __exit__(self, *args: object) -> None:
                self.close()

            def read(self, size: int = -1) -> bytes:
                if self.failure is not None:
                    raise self.failure
                if self.read_once:
                    return b""
                self.read_once = True
                return self.payload or b""

            def close(self) -> None:
                self.closed = True

        def assert_clean_remote_error(error: BaseException, *, expected_status: str) -> None:
            self.assertIsInstance(error, TranscriptionError)
            self.assertIn(expected_status, str(error))
            self.assertIsNone(error.__cause__)
            self.assertIsNone(error.__context__)
            rendered = "".join(
                (
                    str(error),
                    repr(error),
                    repr(error.args),
                    repr(error.__cause__),
                    repr(error.__context__),
                    repr(getattr(error, "__notes__", ())),
                    "".join(traceback.format_exception(error)),
                )
            )
            self.assertNotIn(secret, rendered)
            self.assertNotIn(url, rendered)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "sample.wav"
            audio.write_bytes(b"audio")

            initial_body = Body(failure=OSError(secret))
            initial_error = urllib.error.HTTPError(url, 401, secret, {}, initial_body)
            with mock.patch(
                "speed_of_cinnamon.transcriber._open_http_request",
                side_effect=initial_error,
            ):
                with self.assertRaises(TranscriptionError) as raised:
                    transcribe_with_openai_compatible_api(
                        audio,
                        "en",
                        root / "initial-error.txt",
                        model="local-transcriber",
                        url=url,
                        write_transcript=False,
                    )
            assert_clean_remote_error(raised.exception, expected_status="(401)")
            self.assertTrue(initial_body.closed)

            fallback_first_body = Body(
                b'{"error":{"message":"service_tier not enabled for this project"}}'
            )
            fallback_second_body = Body(failure=ValueError(secret))
            fallback_errors = (
                urllib.error.HTTPError(url, 400, "Bad Request", {}, fallback_first_body),
                urllib.error.HTTPError(url, 503, secret, {}, fallback_second_body),
            )
            requests = 0

            def fallback_open(*args: object, **kwargs: object) -> object:
                nonlocal requests
                error = fallback_errors[requests]
                requests += 1
                raise error

            with mock.patch(
                "speed_of_cinnamon.transcriber._open_http_request",
                side_effect=fallback_open,
            ):
                with self.assertRaises(TranscriptionError) as raised:
                    transcribe_with_openai_compatible_api(
                        audio,
                        "en",
                        root / "fallback-error.txt",
                        model="gpt-4o-transcribe",
                        url="https://api.openai.com/v1",
                        openai_compatible_service_tier_fallback=True,
                        write_transcript=False,
                    )
            assert_clean_remote_error(raised.exception, expected_status="(503)")
            self.assertEqual(requests, 2)
            self.assertTrue(fallback_first_body.closed)
            self.assertTrue(fallback_second_body.closed)

            success_body = Body(b'{"text":"json success"}')
            with mock.patch(
                "speed_of_cinnamon.transcriber._open_http_request",
                return_value=success_body,
            ):
                result = transcribe_with_openai_compatible_api(
                    audio,
                    "en",
                    root / "json-success.txt",
                    model="local-transcriber",
                    url=url,
                    write_transcript=False,
                )
            self.assertEqual(result, "json success")

            for exception_type in (KeyboardInterrupt, SystemExit, GeneratorExit):
                with self.subTest(failure=exception_type.__name__):
                    control_flow = exception_type("/srv/private/control-flow-token")
                    body = Body(failure=control_flow)
                    error = urllib.error.HTTPError(url, 502, "Bad Gateway", {}, body)
                    with mock.patch(
                        "speed_of_cinnamon.transcriber._open_http_request",
                        side_effect=error,
                    ):
                        with self.assertRaises(exception_type) as raised:
                            transcribe_with_openai_compatible_api(
                                audio,
                                "en",
                                root / "control-flow.txt",
                                model="local-transcriber",
                                url=url,
                                write_transcript=False,
                            )
                    self.assertIs(raised.exception, control_flow)

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

            self.assertNotIn("http://127.0.0.1:8000", str(raised.exception))
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
                with self.assertRaisesRegex(TranscriptionError, "is not reachable: \\[redacted remote error\\]") as raised:
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

    def test_openai_compatible_api_rejects_empty_hostname_and_whitespace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            for url, message in (
                ("https://:443/v1", "missing hostname"),
                ("https://example.com /v1", "contains invalid whitespace"),
            ):
                with self.subTest(url=url):
                    with mock.patch(
                        "speed_of_cinnamon.transcriber._open_http_request",
                        side_effect=AssertionError("http request attempted"),
                    ):
                        with self.assertRaisesRegex(TranscriptionError, message):
                            transcribe(
                                audio,
                                "en",
                                Path(tmp) / "sample.txt",
                                backend="openai-compatible",
                                openai_compatible_model="gpt-4o-transcribe",
                                openai_compatible_url=url,
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
            default_model = Path(tmp) / "ct2-model"
            default_model.mkdir()

            with (
                mock.patch("speed_of_cinnamon.transcriber.transcribe_with_faster_whisper", return_value="ok transcript"),
                mock.patch(
                    "speed_of_cinnamon.transcriber.default_ctranslate2_model_path",
                    return_value=str(default_model),
                ),
                mock.patch("speed_of_cinnamon.transcriber.model_supports_language", return_value=True),
            ):
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

    def test_non_openai_backend_ignores_irrelevant_openai_compatible_options(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text_path = Path(tmp) / "sample.txt"
            result = transcribe(
                audio,
                "en",
                text_path,
                backend="command",
                command_template="printf ok",
                openai_compatible_model="bad\x00model",
                openai_compatible_url=None,  # type: ignore[arg-type]
                openai_compatible_api_key="bad\r\nkey",
                openai_compatible_flex_processing="false",  # type: ignore[arg-type]
                openai_compatible_service_tier_fallback=1,  # type: ignore[arg-type]
            )
        self.assertEqual(result, "ok")

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

    def test_faster_whisper_timeout_includes_model_initialization(self) -> None:
        class WhisperModel:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def transcribe(self, *_args: object, **_kwargs: object) -> tuple[list[object], object]:
                self.fail("transcribe should not run after model initialization timeout")

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
                mock.patch("speed_of_cinnamon.transcriber.time.monotonic", side_effect=(0, 0, 901)),
            ):
                with self.assertRaisesRegex(TranscriptionError, "faster-whisper timed out"):
                    transcriber_module.transcribe_with_faster_whisper(audio, "en", text_path, str(model_path))

    def test_faster_whisper_availability_fails_closed_on_native_import_error(self) -> None:
        original_import = __import__

        def fail_import(name: str, *args: object, **kwargs: object) -> object:
            if name == "faster_whisper":
                raise OSError("missing native library")
            return original_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=fail_import):
            self.assertFalse(transcriber_module.faster_whisper_available())

    def test_faster_whisper_direct_helper_reports_native_import_error(self) -> None:
        original_import = __import__

        def fail_import(name: str, *args: object, **kwargs: object) -> object:
            if name == "faster_whisper":
                raise OSError("missing native library")
            return original_import(name, *args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text_path = Path(tmp) / "sample.txt"
            model_path = Path(tmp) / "model"
            model_path.mkdir()
            with mock.patch("builtins.__import__", side_effect=fail_import):
                with self.assertRaisesRegex(TranscriptionError, "faster-whisper could not be loaded"):
                    transcriber_module.transcribe_with_faster_whisper(audio, "en", text_path, str(model_path))

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

    def test_whisper_cpp_direct_helper_rejects_symlinked_transcript_parent_before_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            real_dir = Path(tmp) / "real-transcripts"
            real_dir.mkdir()
            link_dir = Path(tmp) / "link-transcripts"
            link_dir.symlink_to(real_dir, target_is_directory=True)
            model = Path(tmp) / "ggml-base.bin"
            model.write_bytes(b"model")

            with (
                mock.patch("speed_of_cinnamon.transcriber.resolve_whisper_cpp_command", return_value="whisper-cli"),
                mock.patch("speed_of_cinnamon.transcriber.model_supports_language", return_value=True),
                mock.patch("speed_of_cinnamon.transcriber._run_limited_process") as mocked_run,
            ):
                with self.assertRaisesRegex(TranscriptionError, "transcript path must not pass through a symlink"):
                    transcribe_with_whisper_cpp(audio, "en", link_dir / "sample.txt", str(model))

        self.assertFalse(mocked_run.called)

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

    def test_whitespace_whisper_model_uses_backend_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "sample.txt"
            cases = (
                ("whisper-cpp", "default_whisper_cpp_model_path", "transcribe_with_whisper_cpp"),
                ("faster-whisper", "default_ctranslate2_model_path", "transcribe_with_faster_whisper"),
            )
            for backend, default_path, helper in cases:
                with self.subTest(backend=backend):
                    default_model = Path(tmp) / f"{backend}-default"
                    if backend == "whisper-cpp":
                        default_model.write_bytes(b"model")
                    else:
                        default_model.mkdir()
                    with (
                        mock.patch(
                            f"speed_of_cinnamon.transcriber.{default_path}",
                            return_value=str(default_model),
                        ),
                        mock.patch("speed_of_cinnamon.transcriber.model_supports_language", return_value=True),
                        mock.patch(f"speed_of_cinnamon.transcriber.{helper}", return_value="ok") as mocked_helper,
                    ):
                        self.assertEqual(
                            transcribe(audio, "en", text, backend=backend, whisper_model="   "),
                            "ok",
                        )
                    self.assertEqual(mocked_helper.call_args.args[3], str(default_model))

    def test_openai_whisper_retries_transient_generated_cleanup_once(self) -> None:
        real_remove = transcriber_module._remove_generated_transcript_file
        remove_calls: list[Path] = []

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            generated.write_text("hello whisper\n", encoding="utf-8")
            command = args[0] if args else kwargs["args"]
            assert isinstance(command, list)
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        def fail_once(
            path: Path,
            *,
            field_name: str = "generated transcript",
            expected_target: ExpectedTarget,
        ) -> None:
            remove_calls.append(path)
            if len(remove_calls) == 1:
                raise TranscriptionError("transient cleanup failure")
            real_remove(path, field_name=field_name, expected_target=expected_target)

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "result.txt"
            generated = Path(tmp) / "sample.txt"
            with (
                mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper"),
                mock.patch("speed_of_cinnamon.transcriber._run_transcriber_process", side_effect=fake_run),
                mock.patch(
                    "speed_of_cinnamon.transcriber._remove_generated_transcript_file",
                    side_effect=fail_once,
                ),
            ):
                result = transcribe_with_openai_whisper(audio, "en", text)

            self.assertEqual(result, "hello whisper")
            self.assertEqual(remove_calls[:2], [generated, generated])
            self.assertFalse(generated.exists())

    def test_openai_whisper_cleanup_retry_preserves_postcommit_replacement(self) -> None:
        real_remove = transcriber_module._remove_generated_transcript_file
        remove_calls: list[Path] = []

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            generated.write_text("hello whisper\n", encoding="utf-8")
            command = args[0] if args else kwargs["args"]
            assert isinstance(command, list)
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        def replace_then_fail(
            path: Path,
            *,
            field_name: str = "generated transcript",
            expected_target: ExpectedTarget,
        ) -> None:
            remove_calls.append(path)
            if len(remove_calls) == 1:
                path.unlink()
                path.write_text("foreign replacement\n", encoding="utf-8")
                raise TranscriptionError("postcommit cleanup failure")
            real_remove(path, field_name=field_name, expected_target=expected_target)

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            text = Path(tmp) / "result.txt"
            generated = Path(tmp) / "sample.txt"
            with (
                mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper"),
                mock.patch("speed_of_cinnamon.transcriber._run_transcriber_process", side_effect=fake_run),
                mock.patch(
                    "speed_of_cinnamon.transcriber._remove_generated_transcript_file",
                    side_effect=replace_then_fail,
                ),
            ):
                with self.assertRaisesRegex(
                    TranscriptionCleanupError,
                    "failed to clean up generated transcript",
                ):
                    transcribe_with_openai_whisper(audio, "en", text)

            self.assertEqual(remove_calls[:2], [generated, generated])
            self.assertEqual(generated.read_text(encoding="utf-8"), "foreign replacement\n")

    def test_whisper_cpp_retries_transient_generated_cleanup_once(self) -> None:
        real_remove = transcriber_module._remove_generated_transcript_file
        remove_calls: list[Path] = []

        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            text.write_text("hello whisper cpp\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        def fail_once(
            path: Path,
            *,
            field_name: str = "generated transcript",
            expected_target: ExpectedTarget,
        ) -> None:
            remove_calls.append(path)
            if len(remove_calls) == 1:
                raise TranscriptionError("transient cleanup failure")
            real_remove(path, field_name=field_name, expected_target=expected_target)

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
                    side_effect=fail_once,
                ),
            ):
                result = transcribe_with_whisper_cpp(audio, "en", text, str(model), write_transcript=False)

            self.assertEqual(result, "hello whisper cpp")
            self.assertEqual(remove_calls[:2], [text, text])
            self.assertFalse(text.exists())

    def test_whisper_cpp_cleanup_retry_preserves_postcommit_replacement(self) -> None:
        real_remove = transcriber_module._remove_generated_transcript_file
        remove_calls: list[Path] = []

        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            text.write_text("hello whisper cpp\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        def replace_then_fail(
            path: Path,
            *,
            field_name: str = "generated transcript",
            expected_target: ExpectedTarget,
        ) -> None:
            remove_calls.append(path)
            if len(remove_calls) == 1:
                path.unlink()
                path.write_text("foreign replacement\n", encoding="utf-8")
                raise TranscriptionError("postcommit cleanup failure")
            real_remove(path, field_name=field_name, expected_target=expected_target)

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
                    side_effect=replace_then_fail,
                ),
            ):
                with self.assertRaisesRegex(
                    TranscriptionCleanupError,
                    "failed to clean up generated transcript",
                ):
                    transcribe_with_whisper_cpp(audio, "en", text, str(model), write_transcript=False)

            self.assertEqual(remove_calls[:2], [text, text])
            self.assertEqual(text.read_text(encoding="utf-8"), "foreign replacement\n")

    def test_output_cleanup_candidates_are_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first.txt"
            second = Path(tmp) / "second.txt"
            first.write_text("first\n", encoding="utf-8")
            second.write_text("second\n", encoding="utf-8")
            tracker = transcriber_module._OutputCleanupTracker((first, second))
            snapshots = {first: None, second: None}
            real_remove = transcriber_module._remove_generated_transcript_file

            def fail_first_candidate(
                path: Path,
                *,
                field_name: str = "generated transcript",
                expected_target: ExpectedTarget,
            ) -> None:
                if path == first:
                    raise TranscriptionError("first candidate failed")
                real_remove(
                    path,
                    field_name=field_name,
                    expected_target=expected_target,
                )

            with mock.patch(
                "speed_of_cinnamon.transcriber._remove_generated_transcript_file",
                side_effect=fail_first_candidate,
            ):
                errors = transcriber_module._cleanup_output_candidates(
                    tracker,
                    (first, second),
                    snapshots,
                )

            self.assertEqual(len(errors), 1)
            self.assertTrue(first.exists())
            self.assertFalse(second.exists())

    def test_output_cleanup_candidates_preserve_baseexception_and_continue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first.txt"
            second = Path(tmp) / "second.txt"
            first.write_text("first\n", encoding="utf-8")
            second.write_text("second\n", encoding="utf-8")
            tracker = transcriber_module._OutputCleanupTracker((first, second))
            snapshots = {first: None, second: None}
            interrupt = KeyboardInterrupt("cleanup interrupted")
            cleanup_calls: list[Path] = []
            real_remove = transcriber_module._remove_generated_transcript_file

            def fail_first_candidate(
                path: Path,
                *,
                field_name: str = "generated transcript",
                expected_target: ExpectedTarget,
            ) -> None:
                cleanup_calls.append(path)
                if path == first:
                    raise interrupt
                real_remove(
                    path,
                    field_name=field_name,
                    expected_target=expected_target,
                )

            with mock.patch(
                "speed_of_cinnamon.transcriber._remove_generated_transcript_file",
                side_effect=fail_first_candidate,
            ):
                errors = transcriber_module._cleanup_output_candidates(
                    tracker,
                    (first, second),
                    snapshots,
                )

            self.assertEqual(len(errors), 1)
            self.assertIs(errors[0], interrupt)
            self.assertTrue(first.exists())
            self.assertFalse(second.exists())
            self.assertEqual(cleanup_calls.count(first), 1)
            self.assertIn(second, cleanup_calls)

    def test_success_cleanup_preserves_exception_when_add_note_fails(self) -> None:
        class CleanupFailure(BaseException):
            def add_note(self, note: str) -> None:
                raise KeyboardInterrupt("note append failed")

        cleanup_error = CleanupFailure("cleanup failed")
        with self.assertRaises(TranscriptionError) as raised:
            transcriber_module._raise_cleanup_errors([cleanup_error])

        self.assertIsNot(raised.exception, cleanup_error)
        self.assertIsNone(raised.exception.__cause__)
        self.assertIsNone(raised.exception.__context__)


    def test_openai_whisper_success_cleanup_baseexception_preserves_type(self) -> None:
        for exception_type in (KeyboardInterrupt, SystemExit):
            with self.subTest(exception_type=exception_type.__name__):
                with tempfile.TemporaryDirectory() as tmp:
                    audio = Path(tmp) / "sample.wav"
                    audio.write_bytes(b"audio")
                    text = Path(tmp) / "result.txt"
                    generated = Path(tmp) / "sample.txt"
                    interrupt = exception_type("cleanup interrupted")
                    cleanup_calls: list[Path] = []
                    real_remove = transcriber_module._remove_generated_transcript_file

                    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
                        generated.write_text("hello whisper\n", encoding="utf-8")
                        command = args[0] if args else kwargs["args"]
                        assert isinstance(command, list)
                        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

                    def fail_generated(
                        path: Path,
                        *,
                        field_name: str = "generated transcript",
                        expected_target: ExpectedTarget,
                    ) -> None:
                        cleanup_calls.append(path)
                        if path == generated:
                            raise interrupt
                        real_remove(
                            path,
                            field_name=field_name,
                            expected_target=expected_target,
                        )

                    with (
                        mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value="/usr/bin/whisper"),
                        mock.patch(
                            "speed_of_cinnamon.transcriber._run_transcriber_process",
                            side_effect=fake_run,
                        ),
                        mock.patch(
                            "speed_of_cinnamon.transcriber._remove_generated_transcript_file",
                            side_effect=fail_generated,
                        ),
                    ):
                        with self.assertRaises(exception_type) as raised:
                            transcribe_with_openai_whisper(audio, "en", text)

                    self.assertIsNot(raised.exception, interrupt)
                    self.assertEqual(str(raised.exception), "transcription cleanup interrupted")
                    self.assertIsNone(raised.exception.__cause__)
                    self.assertIsNone(raised.exception.__context__)
                    self.assertEqual(cleanup_calls.count(generated), 1)

    def test_whisper_cpp_success_cleanup_baseexception_preserves_type(self) -> None:
        for exception_type in (KeyboardInterrupt, SystemExit):
            with self.subTest(exception_type=exception_type.__name__):
                with tempfile.TemporaryDirectory() as tmp:
                    audio = Path(tmp) / "sample.wav"
                    audio.write_bytes(b"audio")
                    text = Path(tmp) / "sample.txt"
                    model = Path(tmp) / "ggml-base.bin"
                    model.write_bytes(b"model")
                    interrupt = exception_type("cleanup interrupted")
                    cleanup_calls: list[Path] = []
                    real_remove = transcriber_module._remove_generated_transcript_file

                    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
                        text.write_text("hello whisper cpp\n", encoding="utf-8")
                        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

                    def fail_transcript(
                        path: Path,
                        *,
                        field_name: str = "generated transcript",
                        expected_target: ExpectedTarget,
                    ) -> None:
                        cleanup_calls.append(path)
                        if path == text:
                            raise interrupt
                        real_remove(
                            path,
                            field_name=field_name,
                            expected_target=expected_target,
                        )

                    with (
                        mock.patch(
                            "speed_of_cinnamon.transcriber.resolve_whisper_cpp_command",
                            return_value="whisper-cli",
                        ),
                        mock.patch(
                            "speed_of_cinnamon.transcriber.shutil.which",
                            return_value="/usr/bin/whisper-cli",
                        ),
                        mock.patch(
                            "speed_of_cinnamon.transcriber._run_transcriber_process",
                            side_effect=fake_run,
                        ),
                        mock.patch(
                            "speed_of_cinnamon.transcriber._remove_generated_transcript_file",
                            side_effect=fail_transcript,
                        ),
                    ):
                        with self.assertRaises(exception_type) as raised:
                            transcribe_with_whisper_cpp(
                                audio,
                                "en",
                                text,
                                str(model),
                                write_transcript=False,
                            )

                    self.assertIsNot(raised.exception, interrupt)
                    self.assertEqual(str(raised.exception), "transcription cleanup interrupted")
                    self.assertIsNone(raised.exception.__cause__)
                    self.assertIsNone(raised.exception.__context__)
                    self.assertEqual(cleanup_calls.count(text), 1)


    def test_custom_command_uses_digest_bound_staged_audio_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "sample.wav"
            audio.write_bytes(b"original audio")
            text = root / "result.txt"
            captured: dict[str, object] = {}

            def fake_run(segments: list[list[str]], *_args: object, **_kwargs: object) -> str:
                captured["segments"] = segments
                staged_path = Path(segments[0][1])
                self.assertNotEqual(staged_path, audio)
                self.assertEqual(staged_path.read_bytes(), b"original audio")
                audio.write_bytes(b"attacker replacement")
                self.assertEqual(staged_path.read_bytes(), b"original audio")
                return "safe transcript"

            with (
                mock.patch(
                    "speed_of_cinnamon.transcriber._snapshot_private_file",
                    wraps=transcriber_module._snapshot_private_file,
                ) as snapshot_mock,
                mock.patch(
                    "speed_of_cinnamon.transcriber._command_path",
                    return_value="/usr/bin/printf",
                ),
                mock.patch(
                    "speed_of_cinnamon.transcriber.run_command_chain",
                    side_effect=fake_run,
                ),
            ):
                result = transcribe(
                    audio,
                    "en",
                    text,
                    "printf {audio}",
                    backend="command",
                )

        self.assertEqual(result, "safe transcript")
        self.assertEqual(snapshot_mock.call_count, 1)
        self.assertIn("segments", captured)
        self.assertNotIn(str(audio), str(captured["segments"]))

    def test_custom_command_binds_all_compound_audio_placeholders_without_literal_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "sample.wav"
            audio.write_bytes(b"original audio")
            text = root / "result.txt"
            captured: list[list[str]] = []

            def fake_run(segments: list[list[str]], *_args: object, **_kwargs: object) -> str:
                captured.extend(segments)
                return "safe transcript"

            template = f"printf --audio={{audio}} {{audio}} --mirror={{audio}} --literal={audio}"
            with (
                mock.patch("speed_of_cinnamon.transcriber._command_path", return_value="/usr/bin/printf"),
                mock.patch(
                    "speed_of_cinnamon.transcriber.run_command_chain",
                    side_effect=fake_run,
                ),
            ):
                result = transcribe(audio, "en", text, template, backend="command")

            self.assertEqual(result, "safe transcript")
            self.assertEqual(len(captured), 1)
            command = captured[0]
            staged_path = command[2]
            self.assertNotEqual(staged_path, str(audio))
            self.assertTrue(staged_path.endswith(audio.name))
            self.assertEqual(command[1], f"--audio={staged_path}")
            self.assertEqual(command[3], f"--mirror={staged_path}")
            self.assertEqual(command[4], f"--literal={audio}")

    def test_custom_command_checks_executable_before_output_filesystem(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "sample.wav"
            audio.write_bytes(b"audio")
            text = root / "nested" / "result.txt"
            with (
                mock.patch(
                    "speed_of_cinnamon.transcriber._command_path",
                    side_effect=TranscriptionError("/srv/private/secret-command is unavailable"),
                ),
                mock.patch(
                    "speed_of_cinnamon.transcriber.run_command_chain",
                    side_effect=AssertionError("backend must not run"),
                ),
            ):
                with self.assertRaisesRegex(
                    TranscriptionError,
                    "custom transcriber executable is not available",
                ) as raised:
                    transcribe(audio, "en", text, "secret-command {audio}", backend="command")

            self.assertFalse(text.parent.exists())
            self.assertNotIn("/srv/private/secret-command", repr(raised.exception))
            self.assertIsNone(raised.exception.__cause__)
            self.assertIsNone(raised.exception.__context__)

    def test_output_lock_cleanup_interrupt_is_sanitized_without_primary(self) -> None:
        for exception_type in (KeyboardInterrupt, SystemExit, GeneratorExit):
            with self.subTest(exception_type=exception_type.__name__), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                real_close = os.close

                def fail_close(fd: int, *, _exception_type: type[BaseException] = exception_type) -> None:
                    real_close(fd)
                    raise _exception_type("/srv/private/lock-secret token=abc")

                lock_context = transcriber_module._transcriber_output_namespace_lock(root)
                lock_context.__enter__()
                with self.assertRaises(exception_type) as raised:
                    with mock.patch("speed_of_cinnamon.transcriber.os.close", side_effect=fail_close):
                        lock_context.__exit__(None, None, None)

                self.assertNotIn("/srv/private/lock-secret", repr(raised.exception))
                self.assertNotIn("token=abc", repr(raised.exception))
                self.assertNotEqual(raised.exception.args, ("/srv/private/lock-secret token=abc",))
                self.assertIsNone(raised.exception.__cause__)
                self.assertIsNone(raised.exception.__context__)
                self.assertTrue(
                    any(
                        note.startswith("transcript output lock release failed")
                        for note in getattr(raised.exception, "__notes__", [])
                    )
                )

    def test_staged_audio_cleanup_interrupt_is_sanitized_without_primary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            interrupt = KeyboardInterrupt("/srv/private/staged-secret token=xyz")
            with mock.patch(
                "speed_of_cinnamon.transcriber._remove_generated_transcript_file",
                side_effect=interrupt,
            ):
                with self.assertRaises(KeyboardInterrupt) as raised:
                    with transcriber_module._staged_audio_file_for_local_backend(audio):
                        pass

            self.assertIsNot(raised.exception, interrupt)
            self.assertNotIn("/srv/private/staged-secret", repr(raised.exception))
            self.assertNotIn("token=xyz", repr(raised.exception))
            self.assertIsNone(raised.exception.__cause__)
            self.assertIsNone(raised.exception.__context__)
            self.assertTrue(
                any(
                    note.startswith("failed to clean up staged audio file")
                    for note in getattr(raised.exception, "__notes__", [])
                )
            )

    def test_public_audio_errors_are_path_and_chain_free(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            secret_path = Path(tmp) / "private-secret" / "token.wav"
            for operation, expected in (
                (
                    lambda: validate_audio_file(secret_path),
                    "audio file is missing or empty",
                ),
                (
                    lambda: transcriber_module._snapshot_private_file(
                        secret_path,
                        field_name="audio file for backend",
                        include_hash=True,
                    ),
                    "failed to snapshot audio file for backend",
                ),
                (
                    lambda: transcriber_module._read_private_file_bytes(
                        secret_path,
                        field_name="audio file for backend",
                    ),
                    "failed to read audio file for backend",
                ),
            ):
                with self.subTest(operation=operation), self.assertRaisesRegex(TranscriptionError, expected) as raised:
                    operation()
                error = raised.exception
                for channel in (str(error), repr(error), repr(error.args), repr(error.__cause__), repr(error.__context__)):
                    self.assertNotIn(str(secret_path), channel)
                self.assertIsNone(error.__cause__)
                self.assertIsNone(error.__context__)
                self.assertTrue(all(str(secret_path) not in note for note in getattr(error, "__notes__", [])))

    def test_locked_run_reuses_preflight_backend_and_command_segments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "sample.wav"
            audio.write_bytes(b"audio")
            text = root / "result.txt"
            with (
                mock.patch(
                    "speed_of_cinnamon.transcriber.resolve_transcriber",
                    return_value="command",
                ) as resolve_mock,
                mock.patch(
                    "speed_of_cinnamon.transcriber._split_transcriber_command",
                    wraps=transcriber_module._split_transcriber_command,
                ) as split_mock,
                mock.patch(
                    "speed_of_cinnamon.transcriber._command_path",
                    return_value="/usr/bin/printf",
                ) as command_path_mock,
                mock.patch(
                    "speed_of_cinnamon.transcriber.run_command_chain",
                    return_value="preflight transcript",
                ),
            ):
                result = transcribe(
                    audio,
                    "en",
                    text,
                    "printf {audio}",
                    backend="command",
                )

        self.assertEqual(result, "preflight transcript")
        self.assertEqual(resolve_mock.call_count, 1)
        self.assertEqual(split_mock.call_count, 1)
        self.assertEqual(command_path_mock.call_count, 1)

    def test_local_backend_copy_hash_reads_audio_bytes_once(self) -> None:
        payload = b"audio" * 100_000
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "sample.wav"
            audio.write_bytes(payload)
            text = root / "result.txt"
            hash_passes: list[int] = []
            read_bytes = 0
            real_sha256 = hashlib.sha256
            real_read = os.read

            class CountingHash:
                def __init__(self) -> None:
                    self._hash = real_sha256()
                    self._pass_index = len(hash_passes)
                    hash_passes.append(0)

                def update(self, data: bytes) -> None:
                    hash_passes[self._pass_index] += len(data)
                    self._hash.update(data)

                def digest(self) -> bytes:
                    return self._hash.digest()

                def hexdigest(self) -> str:
                    return self._hash.hexdigest()

            def counting_read(fd: int, size: int) -> bytes:
                nonlocal read_bytes
                data = real_read(fd, size)
                read_bytes += len(data)
                return data

            def fake_run(command: list[str], **_kwargs: object) -> None:
                Path(command[-1], "sample.txt").write_text("one pass\n", encoding="utf-8")

            with (
                mock.patch(
                    "speed_of_cinnamon.transcriber._command_path",
                    return_value="/usr/bin/whisper",
                ),
                mock.patch(
                    "speed_of_cinnamon.transcriber._run_limited_process",
                    side_effect=fake_run,
                ),
                mock.patch(
                    "speed_of_cinnamon.transcriber.os.read",
                    side_effect=counting_read,
                ),
                mock.patch(
                    "speed_of_cinnamon.transcriber._snapshot_private_file",
                    wraps=transcriber_module._snapshot_private_file,
                ) as snapshot_mock,
                mock.patch(
                    "speed_of_cinnamon.transcriber.hashlib.sha256",
                    side_effect=CountingHash,
                ),
            ):
                result = transcribe(audio, "en", text, backend="whisper")

        self.assertEqual(result, "one pass")
        self.assertEqual(read_bytes, len(payload))
        self.assertEqual(hash_passes[0], len(payload))
        self.assertEqual(snapshot_mock.call_count, 1)
        self.assertFalse(snapshot_mock.call_args.kwargs["include_hash"])

    def test_staged_audio_detects_mutation_during_copy_with_final_source_stat(self) -> None:
        payload = b"a" * 131_072
        mutated = b"b" * len(payload)
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(payload)
            metadata_snapshot = transcriber_module._snapshot_private_file(
                audio,
                field_name="audio file for backend",
                include_hash=False,
            )
            real_sha256 = hashlib.sha256
            mutated_once = False

            class MutatingHash:
                def __init__(self) -> None:
                    self._hash = real_sha256()

                def update(self, data: bytes) -> None:
                    nonlocal mutated_once
                    self._hash.update(data)
                    if not mutated_once:
                        mutated_once = True
                        audio.write_bytes(mutated)

                def digest(self) -> bytes:
                    return self._hash.digest()

                def hexdigest(self) -> str:
                    return self._hash.hexdigest()

            with mock.patch(
                "speed_of_cinnamon.transcriber.hashlib.sha256",
                side_effect=MutatingHash,
            ):
                with self.assertRaisesRegex(TranscriptionError, "failed to stage audio file for backend access"):
                    with transcriber_module._staged_audio_file_for_local_backend(
                        audio,
                        expected_snapshot=metadata_snapshot,
                    ):
                        self.fail("staging unexpectedly succeeded")

            self.assertTrue(mutated_once)

    def test_custom_command_runtime_executable_loss_is_redacted(self) -> None:
        secret = "/srv/private/disappeared-command token=abc123"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "sample.wav"
            audio.write_bytes(b"audio")
            text = root / "result.txt"
            with (
                mock.patch(
                    "speed_of_cinnamon.transcriber._command_path",
                    return_value="/usr/bin/printf",
                ),
                mock.patch(
                    "speed_of_cinnamon.transcriber.run_command_chain",
                    side_effect=CommandChainError(f"transcriber command not found: {secret}"),
                ),
            ):
                with self.assertRaisesRegex(TranscriptionError, "transcriber command not found") as raised:
                    transcribe(audio, "en", text, "printf {audio}", backend="command")

        error = raised.exception
        rendered = "\n".join(
            (
                str(error),
                repr(error),
                repr(error.args),
                repr(error.__cause__),
                repr(error.__context__),
                repr(getattr(error, "__notes__", ())),
            )
        )
        self.assertNotIn(secret, rendered)
        self.assertNotIn("abc123", rendered)
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)

    def test_command_path_resolution_errors_are_chain_free_and_redacted(self) -> None:
        secret = "/srv/private/command-token token=abc123"
        for injected in (TranscriptionError(secret), OSError(secret)):
            with self.subTest(error_type=type(injected).__name__):
                with mock.patch(
                    "speed_of_cinnamon.transcriber._which",
                    side_effect=injected,
                ):
                    with self.assertRaisesRegex(
                        TranscriptionError,
                        "transcriber executable is not available",
                    ) as raised:
                        transcriber_module._command_path("whisper")

                error = raised.exception
                rendered = "\n".join(
                    (
                        str(error),
                        repr(error),
                        repr(error.args),
                        repr(error.__cause__),
                        repr(error.__context__),
                        repr(getattr(error, "__notes__", ())),
                    )
                )
                self.assertNotIn(secret, rendered)
                self.assertNotIn("abc123", rendered)
                self.assertIsNone(error.__cause__)
                self.assertIsNone(error.__context__)

    def test_limited_process_runtime_failures_are_chain_free_and_redacted(self) -> None:
        secret = "/srv/private/spawn-token token=abc123"
        cases = (
            (
                "resolution-transcription",
                mock.patch(
                    "speed_of_cinnamon.transcriber._command_path",
                    side_effect=TranscriptionError(secret),
                ),
                "transcriber executable is not available",
            ),
            (
                "resolution-oserror",
                mock.patch(
                    "speed_of_cinnamon.transcriber._command_path",
                    side_effect=OSError(secret),
                ),
                "transcriber executable is not available",
            ),
            (
                "spawn-oserror",
                mock.patch(
                    "speed_of_cinnamon.transcriber._run_transcriber_process",
                    side_effect=OSError(secret),
                ),
                "failed to run transcriber backend",
            ),
            (
                "spawn-runtimeerror",
                mock.patch(
                    "speed_of_cinnamon.transcriber._run_transcriber_process",
                    side_effect=RuntimeError(secret),
                ),
                "failed to run transcriber backend",
            ),
            (
                "spawn-command-chain",
                mock.patch(
                    "speed_of_cinnamon.transcriber._run_transcriber_process",
                    side_effect=CommandChainError(f"transcriber command execution failed: {secret}"),
                ),
                "transcriber command execution failed",
            ),
            (
                "spawn-timeout",
                mock.patch(
                    "speed_of_cinnamon.transcriber._run_transcriber_process",
                    side_effect=subprocess.TimeoutExpired(secret, 7),
                ),
                "transcription backend timed out after 900s",
            ),
        )
        for name, injected_patch, expected in cases:
            with self.subTest(case=name):
                with mock.patch(
                    "speed_of_cinnamon.transcriber._command_path",
                    return_value="/usr/bin/whisper",
                ):
                    with injected_patch:
                        with self.assertRaisesRegex(TranscriptionError, expected) as raised:
                            _run_limited_process(["whisper", "audio"])

                error = raised.exception
                rendered = "\n".join(
                    (
                        str(error),
                        repr(error),
                        repr(error.args),
                        repr(error.__cause__),
                        repr(error.__context__),
                        repr(getattr(error, "__notes__", ())),
                    )
                )
                self.assertNotIn(secret, rendered)
                self.assertNotIn("abc123", rendered)
                self.assertIsNone(error.__cause__)
                self.assertIsNone(error.__context__)

    def test_openai_top_level_reads_audio_once_and_reuses_body_for_flex_fallback(self) -> None:
        payload = b"audio" * 100_000
        requests: list[object] = []
        read_bytes = 0

        class Response:
            def __init__(self) -> None:
                self._read = False

            def __enter__(self):
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self, size: int = -1) -> bytes:
                if self._read:
                    return b""
                self._read = True
                return b'{"text":"fallback transcript"}'

        class CountingReader:
            def __init__(self, wrapped: object) -> None:
                self._wrapped = wrapped

            def __enter__(self):
                return self

            def __exit__(self, *args: object) -> None:
                self.close()
                return None

            def read(self, size: int = -1) -> bytes:
                nonlocal read_bytes
                data = self._wrapped.read(size)
                read_bytes += len(data)
                return data

            def fileno(self) -> int:
                return self._wrapped.fileno()

            def close(self) -> None:
                self._wrapped.close()

        real_fdopen = transcriber_module.os.fdopen

        def tracked_fdopen(*args: object, **kwargs: object) -> CountingReader:
            return CountingReader(real_fdopen(*args, **kwargs))

        def fake_open(request: object, *, timeout: int = 0, field_name: str = "") -> Response:
            requests.append(request)
            if len(requests) == 1:
                raise urllib.error.HTTPError(
                    request.full_url,
                    400,
                    "Bad Request",
                    {},
                    io.BytesIO(b'{"error":{"message":"service_tier not enabled"}}'),
                )
            return Response()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "sample.wav"
            audio.write_bytes(payload)
            text = root / "api-output" / "sample.txt"
            with (
                mock.patch("speed_of_cinnamon.transcriber.os.fdopen", side_effect=tracked_fdopen),
                mock.patch("speed_of_cinnamon.transcriber._open_http_request", side_effect=fake_open),
            ):
                result = transcribe(
                    audio,
                    "en",
                    text,
                    backend="openai-compatible",
                    openai_compatible_model="gpt-4o-transcribe",
                    openai_compatible_url="https://api.openai.com/v1",
                    openai_compatible_service_tier_fallback=True,
                )

            self.assertFalse(text.parent.exists())

        self.assertEqual(result, "fallback transcript")
        self.assertEqual(len(requests), 2)
        self.assertEqual(read_bytes, len(payload))
        self.assertIn(payload, requests[0].data)
        self.assertIn(payload, requests[1].data)

    def test_openai_top_level_rejects_mutation_during_single_audio_read(self) -> None:
        payload = b"audio" * 100
        mutated = b"muted" * 100
        mutated_once = False

        class MutatingReader:
            def __init__(self, wrapped: object, path: Path) -> None:
                self._wrapped = wrapped
                self._path = path

            def __enter__(self):
                return self

            def __exit__(self, *args: object) -> None:
                self.close()
                return None

            def read(self, size: int = -1) -> bytes:
                nonlocal mutated_once
                data = self._wrapped.read(size)
                if not mutated_once:
                    mutated_once = True
                    self._path.write_bytes(mutated)
                return data

            def fileno(self) -> int:
                return self._wrapped.fileno()

            def close(self) -> None:
                self._wrapped.close()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "sample.wav"
            audio.write_bytes(payload)
            text = root / "api-output" / "sample.txt"
            real_fdopen = transcriber_module.os.fdopen

            def mutating_fdopen(*args: object, **kwargs: object) -> MutatingReader:
                return MutatingReader(real_fdopen(*args, **kwargs), audio)

            with (
                mock.patch("speed_of_cinnamon.transcriber.os.fdopen", side_effect=mutating_fdopen),
                mock.patch(
                    "speed_of_cinnamon.transcriber._open_http_request",
                    side_effect=AssertionError("request must not run after audio mutation"),
                ),
            ):
                with self.assertRaisesRegex(TranscriptionError, "changed between validation and read"):
                    transcribe(
                        audio,
                        "en",
                        text,
                        backend="openai-compatible",
                        openai_compatible_model="gpt-4o-transcribe",
                        openai_compatible_url="https://api.openai.com/v1",
                    )

            self.assertTrue(mutated_once)
            self.assertFalse(text.parent.exists())

    def test_openai_top_level_skips_output_namespace_for_success_and_failure(self) -> None:
        class Response:
            def __init__(self) -> None:
                self._read = False

            def __enter__(self):
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self, size: int = -1) -> bytes:
                if self._read:
                    return b""
                self._read = True
                return b'{"text":"api transcript"}'

        for failed in (False, True):
            with self.subTest(failed=failed), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                audio = root / "sample.wav"
                audio.write_bytes(b"audio")
                text = root / "api-output" / "sample.txt"
                request_error = OSError("network unavailable") if failed else None
                http_patch = (
                    mock.patch(
                        "speed_of_cinnamon.transcriber._open_http_request",
                        side_effect=request_error,
                    )
                    if failed
                    else mock.patch(
                        "speed_of_cinnamon.transcriber._open_http_request",
                        return_value=Response(),
                    )
                )
                with (
                    mock.patch(
                        "speed_of_cinnamon.transcriber.ensure_directory_without_following_symlinks",
                        side_effect=AssertionError("API must not create transcript directory"),
                    ),
                    mock.patch(
                        "speed_of_cinnamon.transcriber._transcriber_output_namespace_lock",
                        side_effect=AssertionError("API must not acquire transcript lock"),
                    ),
                    mock.patch(
                        "speed_of_cinnamon.transcriber.tempfile.mkdtemp",
                        side_effect=AssertionError("API must not create staging directory"),
                    ),
                    http_patch,
                ):
                    if failed:
                        with self.assertRaisesRegex(TranscriptionError, "is not reachable"):
                            transcribe(
                                audio,
                                "en",
                                text,
                                backend="openai-compatible",
                                openai_compatible_model="gpt-4o-transcribe",
                                openai_compatible_url="https://api.openai.com/v1",
                            )
                    else:
                        self.assertEqual(
                            transcribe(
                                audio,
                                "en",
                                text,
                                backend="openai-compatible",
                                openai_compatible_model="gpt-4o-transcribe",
                                openai_compatible_url="https://api.openai.com/v1",
                            ),
                            "api transcript",
                        )
                self.assertFalse(text.parent.exists())

    def test_openai_top_level_allows_parallel_http_calls_without_namespace_lock(self) -> None:
        watchdog_seconds = 10.0
        first_http_entered = threading.Event()
        second_http_entered = threading.Event()
        release_http = threading.Event()
        overlap = threading.Event()
        state_lock = threading.Lock()
        active_calls = 0
        max_active_calls = 0
        http_calls = 0

        class Response:
            def __init__(self) -> None:
                self._read = False

            def __enter__(self):
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self, size: int = -1) -> bytes:
                if self._read:
                    return b""
                self._read = True
                return b'{"text":"parallel transcript"}'

        def fake_open(request: object, *, timeout: int = 0, field_name: str = "") -> Response:
            nonlocal active_calls, max_active_calls, http_calls
            with state_lock:
                http_calls += 1
                active_calls += 1
                max_active_calls = max(max_active_calls, active_calls)
                if http_calls == 1:
                    first_http_entered.set()
                elif http_calls == 2:
                    second_http_entered.set()
                if active_calls >= 2:
                    overlap.set()
            try:
                if not release_http.wait(watchdog_seconds):
                    raise AssertionError("HTTP test watchdog expired")
                return Response()
            finally:
                with state_lock:
                    active_calls -= 1

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "sample.wav"
            audio.write_bytes(b"audio")
            results: list[str | None] = [None, None]
            errors: list[BaseException | None] = [None, None]

            def worker(index: int) -> None:
                try:
                    results[index] = transcribe(
                        audio,
                        "en",
                        root / f"api-output-{index}" / "sample.txt",
                        backend="openai-compatible",
                        openai_compatible_model="gpt-4o-transcribe",
                        openai_compatible_url="https://api.openai.com/v1",
                    )
                except BaseException as exc:
                    errors[index] = exc

            with mock.patch("speed_of_cinnamon.transcriber._open_http_request", side_effect=fake_open):
                threads = [threading.Thread(target=worker, args=(index,)) for index in range(2)]
                for thread in threads:
                    thread.daemon = True
                deadline = time.monotonic() + watchdog_seconds
                try:
                    for thread in threads:
                        thread.start()
                    first_seen = first_http_entered.wait(max(0.0, deadline - time.monotonic()))
                    second_seen = second_http_entered.wait(max(0.0, deadline - time.monotonic()))
                    overlap_seen = overlap.wait(max(0.0, deadline - time.monotonic()))
                finally:
                    release_http.set()
                    for thread in threads:
                        thread.join(max(0.0, deadline - time.monotonic()))

            self.assertTrue(all(not thread.is_alive() for thread in threads))
            self.assertFalse((root / "api-output-0").exists())
            self.assertFalse((root / "api-output-1").exists())
            if any(errors):
                self.fail("parallel worker failed: " + repr(next(error for error in errors if error is not None)))
        self.assertTrue(first_seen)
        self.assertTrue(second_seen)
        self.assertTrue(overlap_seen)
        self.assertEqual(max_active_calls, 2)
        self.assertEqual(http_calls, 2)
        self.assertEqual(results, ["parallel transcript", "parallel transcript"])

if __name__ == "__main__":
    unittest.main()
