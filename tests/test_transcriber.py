from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from speed_of_cinnamon.transcriber import render_command_template, transcribe


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


if __name__ == "__main__":
    unittest.main()
