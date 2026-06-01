from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from speed_of_cinnamon.settings_export import SettingsExportError, build_export, read_export, write_export


class SettingsExportTest(unittest.TestCase):
    def test_build_export_keeps_only_supported_settings(self) -> None:
        payload = build_export({
            "language": "de",
            "append-space": False,
            "auto-transcribe-timeout": "false",
            "sanitize-special-chars": "true",
            "typing-delay-ms": "12",
            "post-process-backend": "ollama",
            "ollama-model": "llama3.2:3b",
            "openai-compatible-url": "http://127.0.0.1:8000/v1",
            "openai-compatible-model": "local-llama",
            "cli-path": "/tmp/not-portable",
            "unknown": "ignored",
        })
        settings = payload["settings"]
        self.assertEqual(payload["app"], "speed-of-cinnamon")
        self.assertEqual(settings["language"], "de")
        self.assertFalse(settings["append-space"])
        self.assertFalse(settings["auto-transcribe-timeout"])
        self.assertTrue(settings["sanitize-special-chars"])
        self.assertEqual(settings["typing-delay-ms"], 12)
        self.assertEqual(settings["post-process-backend"], "ollama")
        self.assertEqual(settings["ollama-model"], "llama3.2:3b")
        self.assertEqual(settings["openai-compatible-url"], "http://127.0.0.1:8000/v1")
        self.assertEqual(settings["openai-compatible-model"], "local-llama")
        self.assertNotIn("cli-path", settings)
        self.assertNotIn("unknown", settings)

    def test_write_and_read_export_round_trips_normalized_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            write_export(path, {
                "auto-transcribe-timeout": False,
                "notify-complete": "false",
                "personal-context": "Project words",
            })
            payload = read_export(path)
        self.assertFalse(payload["settings"]["auto-transcribe-timeout"])
        self.assertFalse(payload["settings"]["notify-complete"])
        self.assertEqual(payload["settings"]["personal-context"], "Project words")

    def test_read_export_rejects_other_app(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text('{"app":"other","version":1,"settings":{}}\n', encoding="utf-8")
            with self.assertRaisesRegex(SettingsExportError, "different app"):
                read_export(path)


if __name__ == "__main__":
    unittest.main()
