from __future__ import annotations

import tempfile
import unittest
from unittest import mock
from pathlib import Path

from speed_of_cinnamon.settings_export import (
    MAX_SETTINGS_EXPORT_BYTES,
    SettingsExportError,
    build_export,
    read_export,
    write_export,
)


class SettingsExportTest(unittest.TestCase):
    def test_write_export_rejects_null_byte_path(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "invalid null byte"):
            write_export(Path("settings\x00.json"), {"language": "en"})

    def test_read_export_rejects_null_byte_path(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "invalid null byte"):
            read_export(Path("settings\x00.json"))

    def test_read_export_rejects_oversized_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text("x" * (MAX_SETTINGS_EXPORT_BYTES + 1), encoding="utf-8")
            with self.assertRaisesRegex(SettingsExportError, "settings export is too large"):
                read_export(path)

    def test_build_export_keeps_only_supported_settings(self) -> None:
        payload = build_export({
            "primary-language-keybinding": "<Super><Alt>z::",
            "secondary-language-keybinding": "<Super><Shift>z::",
            "language": "de",
            "append-space": False,
            "auto-transcribe-timeout": "false",
            "keep-recording-artifacts": "true",
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
        self.assertEqual(settings["primary-language-keybinding"], "<Super><Alt>z::")
        self.assertEqual(settings["secondary-language-keybinding"], "<Super><Shift>z::")
        self.assertEqual(settings["language"], "de")
        self.assertFalse(settings["append-space"])
        self.assertFalse(settings["auto-transcribe-timeout"])
        self.assertTrue(settings["keep-recording-artifacts"])
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
            write_export(
                path,
                {
                    "auto-transcribe-timeout": False,
                    "notify-complete": "false",
                    "personal-context": "Project words",
                },
                {
                    "alarms": [
                        {
                            "id": "alarm-0900",
                            "name": "Standup",
                            "hour": 9,
                            "minute": 0,
                            "days": ["mon", "wed", "fri"],
                            "enabled": True,
                            "urgency": "critical",
                            "last_triggered_at": "2026-06-01T09:00",
                        }
                    ],
                    "last_checked_at": "2026-06-01T09:10",
                },
            )
            payload = read_export(path)
        self.assertFalse(payload["settings"]["auto-transcribe-timeout"])
        self.assertFalse(payload["settings"]["notify-complete"])
        self.assertEqual(payload["settings"]["personal-context"], "Project words")
        self.assertEqual(payload["alarms"]["last_checked_at"], "2026-06-01T09:10")
        self.assertEqual(payload["alarms"]["alarms"][0]["name"], "Standup")
        self.assertEqual(payload["alarms"]["alarms"][0]["days"], ["mon", "wed", "fri"])

    def test_read_legacy_export_without_alarms_uses_empty_alarm_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text('{"app":"speed-of-cinnamon","version":1,"settings":{"language":"de"}}\n', encoding="utf-8")
            payload = read_export(path)
        self.assertEqual(payload["settings"]["language"], "de")
        self.assertEqual(payload["alarms"], {"version": 1, "alarms": [], "last_checked_at": ""})

    def test_read_export_rejects_other_app(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text('{"app":"other","version":1,"settings":{}}\n', encoding="utf-8")
            with self.assertRaisesRegex(SettingsExportError, "different app"):
                read_export(path)

    @mock.patch("speed_of_cinnamon.settings_export.os.replace", side_effect=OSError("disk full"))
    def test_write_export_raises_when_atomic_replace_fails(self, mocked_replace: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            with self.assertRaisesRegex(SettingsExportError, "failed to write settings export"):
                write_export(path, {"language": "en"})
        mocked_replace.assert_called_once()


if __name__ == "__main__":
    unittest.main()
