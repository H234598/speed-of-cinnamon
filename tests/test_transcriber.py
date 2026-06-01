from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from speed_of_cinnamon.transcriber import (
    TranscriberConfig,
    TranscriptionError,
    normalize_backend,
    render_command_template,
    resolve_transcriber,
    transcribe,
)


class TranscriberTest(unittest.TestCase):
    def test_template_quotes_placeholders(self) -> None:
        rendered = render_command_template(
            "tool --audio {audio} --lang {language} --text {text}",
            Path("/tmp/with space/audio.wav"),
            "de",
            Path("/tmp/out text.txt"),
        )
        self.assertIn("'/tmp/with space/audio.wav'", rendered)
        self.assertIn("--lang de", rendered)
        self.assertIn("'/tmp/out text.txt'", rendered)

    def test_command_stdout_is_saved_as_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"not really wav but enough for command-template test")
            text = Path(tmp) / "sample.txt"
            result = transcribe(audio, "en", text, "printf 'hello cinnamon'")
            saved = text.read_text(encoding="utf-8").strip()
        self.assertEqual(result, "hello cinnamon")
        self.assertEqual(saved, "hello cinnamon")

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
        with mock.patch("speed_of_cinnamon.transcriber.shutil.which", side_effect=lambda name: "/usr/bin/whisper" if name == "whisper" else None):
            self.assertEqual(resolve_transcriber(config), "whisper")

    def test_auto_uses_whisper_cpp_when_model_is_configured(self) -> None:
        def which(command: str) -> str | None:
            return "/usr/bin/whisper-cli" if command == "whisper-cli" else None

        config = TranscriberConfig(whisper_model="/models/ggml-base.bin")
        with mock.patch("speed_of_cinnamon.transcriber.shutil.which", side_effect=which):
            self.assertEqual(resolve_transcriber(config), "whisper-cpp")

    def test_auto_reports_missing_transcriber(self) -> None:
        with mock.patch("speed_of_cinnamon.transcriber.shutil.which", return_value=None):
            with self.assertRaisesRegex(TranscriptionError, "no transcriber available"):
                resolve_transcriber(TranscriberConfig())

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
                mock.patch("speed_of_cinnamon.transcriber.resolve_whisper_cpp_command", return_value="whisper-cli"),
                self.assertRaisesRegex(TranscriptionError, "model path is required"),
            ):
                transcribe(audio, "en", Path(tmp) / "sample.txt", backend="whisper-cpp")


if __name__ == "__main__":
    unittest.main()
