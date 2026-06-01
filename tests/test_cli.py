from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from speed_of_cinnamon import cli


class CliTest(unittest.TestCase):
    def test_insert_text_can_be_disabled(self) -> None:
        with redirect_stdout(io.StringIO()):
            code = cli.run(["insert-text", "hello", "--insert-method", "none", "--json"])
        self.assertEqual(code, 0)

    def test_transcribe_file_with_command_template(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(io.StringIO()):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--transcriber-command",
                    "printf test",
                    "--json",
                ])
        self.assertEqual(code, 0)

    @mock.patch("speed_of_cinnamon.cli.command_start")
    def test_toggle_starts_when_idle(self, mocked_start: mock.Mock) -> None:
        mocked_start.return_value = {"status": "recording"}
        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stdout(io.StringIO()):
                code = cli.run(["toggle", "--state-file", str(Path(tmp) / "state.json"), "--json"])
        self.assertEqual(code, 0)
        mocked_start.assert_called_once()


if __name__ == "__main__":
    unittest.main()
