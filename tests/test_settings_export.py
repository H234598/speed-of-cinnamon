from __future__ import annotations

import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from speed_of_cinnamon.settings_export import (
    MAX_SETTINGS_EXPORT_BYTES,
    MAX_SETTINGS_TEXT_CHARS,
    MAX_SETTINGS_EXPORT_PATH_CHARS,
    MAX_TYPING_DELAY_MS,
    MAX_ALARM_COUNT,
    _sanitize_text_field,
    SettingsExportError,
    normalize_setting,
    normalize_alarm_store,
    MAX_RECORDING_SECONDS,
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

    def test_read_export_rejects_escaped_null_path(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "invalid null byte"):
            read_export(Path("settings\\\\x00.json"))

    def test_read_export_rejects_non_path(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "must be a path"):
            read_export("settings-export.json")  # type: ignore[arg-type]

    def test_read_export_rejects_boolean_path(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "must be a path"):
            read_export(True)  # type: ignore[arg-type]

    def test_write_export_rejects_non_path(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "must be a path"):
            write_export("settings-export.json", {"language": "en"})  # type: ignore[arg-type]

    def test_write_export_rejects_boolean_path(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "must be a path"):
            write_export(False, {"language": "en"})  # type: ignore[arg-type]

    def test_sanitize_text_field_rejects_non_text(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "must be text"):
            _sanitize_text_field(1, field_name="setting value")

    def test_sanitize_text_field_rejects_boolean(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "must be text"):
            _sanitize_text_field(True, field_name="setting value")

    def test_sanitize_text_field_rejects_control_character(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "invalid control character"):
            _sanitize_text_field("value\\rextra", field_name="setting value")

    def test_read_export_rejects_oversized_path(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "path is invalid"):
            read_export(Path("a" * (MAX_SETTINGS_EXPORT_PATH_CHARS + 1)))

    def test_read_export_rejects_oversized_path_bytes(self) -> None:
        with mock.patch("speed_of_cinnamon.settings_export.MAX_SETTINGS_EXPORT_PATH_CHARS", 4):
            with self.assertRaisesRegex(SettingsExportError, "path is invalid"):
                read_export(Path("é" * 3))

    def test_read_export_rejects_oversized_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text("x" * (MAX_SETTINGS_EXPORT_BYTES + 1), encoding="utf-8")
            with self.assertRaisesRegex(SettingsExportError, "settings export is too large"):
                read_export(path)

    def test_sanitize_text_field_rejects_oversized_text_bytes(self) -> None:
        with mock.patch("speed_of_cinnamon.settings_export.MAX_SETTINGS_TEXT_CHARS", 4):
            with self.assertRaisesRegex(SettingsExportError, "is too long"):
                _sanitize_text_field("😀" * 2, field_name="setting value")

    def test_write_export_rejects_oversized_path(self) -> None:
        path = Path("a" * (MAX_SETTINGS_EXPORT_PATH_CHARS + 1))
        with self.assertRaisesRegex(SettingsExportError, "path is invalid"):
            write_export(path, {"language": "en"})

    def test_read_export_rejects_invalid_utf8_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_bytes(b"\xff")
            with self.assertRaisesRegex(SettingsExportError, "settings export could not be read"):
                read_export(path)

    def test_read_export_rejects_control_char_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text('{"app":"speed-of-cinnamon","version":2,"created_at":"","speed_of_cinnamon_version":"",'
                            '"settings":{"language":"en\\rde","max-seconds":30},'
                            '"alarms":{"version":2,"alarms":[],"last_checked_at":""}}', encoding="utf-8")
            with self.assertRaisesRegex(SettingsExportError, "invalid control character"):
                read_export(path)

    def test_build_export_keeps_only_supported_settings(self) -> None:
        payload = build_export({
            "primary-language-keybinding": "<Super><Alt>z::",
            "secondary-language-keybinding": "<Super><Shift>z::",
            "language": "de",
            "append-space": False,
            "auto-transcribe-timeout": False,
            "keep-recording-artifacts": True,
            "sanitize-special-chars": True,
            "typing-delay-ms": "12",
            "post-process-backend": "ollama",
            "ollama-model": "llama3.2:3b",
            "openai-compatible-url": "http://127.0.0.1:8000/v1",
            "openai-compatible-model": "local-llama",
            "openai-compatible-text-model": "local-polisher",
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
        self.assertEqual(settings["openai-compatible-text-model"], "local-polisher")
        self.assertNotIn("cli-path", settings)
        self.assertNotIn("unknown", settings)

    def test_write_and_read_export_round_trips_normalized_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            write_export(
                path,
                {
                    "auto-transcribe-timeout": False,
                    "notify-complete": False,
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

    def test_write_export_rejects_out_of_range_numeric_settings(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "must be at least"):
            build_export({"max-seconds": -2})
        with self.assertRaisesRegex(SettingsExportError, "must be at most"):
            build_export({"typing-delay-ms": MAX_TYPING_DELAY_MS + 1})

    def test_write_export_rejects_non_integer_numeric_settings(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "must be an integer"):
            build_export({"typing-delay-ms": "bad"})
        with self.assertRaisesRegex(SettingsExportError, "must be an integer"):
            build_export({"typing-delay-ms": 12.5})

    def test_read_export_rejects_out_of_range_numeric_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text(
                '{"app":"speed-of-cinnamon","version":2,"settings":{"max-seconds":-2},'
                '"alarms":{"version":2,"alarms":[],"last_checked_at":""}}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SettingsExportError, "must be at least"):
                read_export(path)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text(
                '{"app":"speed-of-cinnamon","version":2,"settings":{"typing-delay-ms":'
                + str(MAX_TYPING_DELAY_MS + 1)
                + '},'
                + '"alarms":{"version":2,"alarms":[],"last_checked_at":""}}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SettingsExportError, "must be at most"):
                read_export(path)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text(
                '{"app":"speed-of-cinnamon","version":2,"settings":{"max-seconds":"bad"},'
                '"alarms":{"version":2,"alarms":[],"last_checked_at":""}}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SettingsExportError, "must be an integer"):
                read_export(path)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text(
                '{"app":"speed-of-cinnamon","version":2,"settings":{"max-seconds":12.5},'
                '"alarms":{"version":2,"alarms":[],"last_checked_at":""}}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SettingsExportError, "must be an integer"):
                read_export(path)

    def test_read_export_rejects_string_boolean_setting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text(
                '{"app":"speed-of-cinnamon","version":2,"settings":{"auto-transcribe-timeout":"true"},'
                '"alarms":{"version":2,"alarms":[],"last_checked_at":""}}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SettingsExportError, "must be a boolean"):
                read_export(path)

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

    def test_read_export_rejects_boolean_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text('{"app":"speed-of-cinnamon","version":true,"settings":{"language":"en"}}', encoding="utf-8")
            with self.assertRaisesRegex(SettingsExportError, "unsupported settings export version"):
                read_export(path)

    def test_read_export_rejects_v2_payload_without_alarms(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text('{"app":"speed-of-cinnamon","version":2,"settings":{"language":"de"}}', encoding="utf-8")
            with self.assertRaisesRegex(SettingsExportError, "settings export alarms must be an object"):
                read_export(path)

    def test_read_export_rejects_non_object_alarms_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text(
                '{"app":"speed-of-cinnamon","version":2,"settings":{"language":"de"},"alarms":[]}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SettingsExportError, "settings export alarms must be an object"):
                read_export(path)

    def test_read_export_rejects_non_list_alarm_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text(
                '{"app":"speed-of-cinnamon","version":2,"settings":{"language":"de"},'
                '"alarms":{"version":2,"alarms":{},"last_checked_at":""}}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SettingsExportError, "settings export alarms must be a list"):
                read_export(path)

    def test_normalize_alarm_store_rejects_non_object_payload(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "settings export alarms must be an object"):
            normalize_alarm_store(True)  # type: ignore[arg-type]

    def test_read_export_rejects_boolean_numeric_setting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text(
                '{"app":"speed-of-cinnamon","version":2,"settings":{"max-seconds":true},'
                '"alarms":{"version":2,"alarms":[],"last_checked_at":""}}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SettingsExportError, "setting max-seconds must be an integer"):
                read_export(path)

    def test_build_export_rejects_boolean_numeric_setting(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "setting max-seconds must be an integer"):
            build_export({"max-seconds": True})
        with self.assertRaisesRegex(SettingsExportError, "setting typing-delay-ms must be an integer"):
            build_export({"typing-delay-ms": False})

    def test_build_export_rejects_non_object_alarm_store(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "settings export alarms must be an object"):
            build_export({"language": "en"}, alarm_store=True)  # type: ignore[arg-type]
        with self.assertRaisesRegex(SettingsExportError, "settings export alarms must be an object"):
            build_export({"language": "en"}, alarm_store=[])  # type: ignore[arg-type]

    @mock.patch("speed_of_cinnamon.settings_export.os.replace", side_effect=OSError("disk full"))
    def test_write_export_raises_when_atomic_replace_fails(self, mocked_replace: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            with self.assertRaisesRegex(SettingsExportError, "failed to write settings export"):
                write_export(path, {"language": "en"})
        mocked_replace.assert_called_once()

    def test_write_export_sets_private_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            write_export(path, {"language": "en"})
            mode = path.stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)

    def test_read_export_rejects_null_byte_setting_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text(
                '{"app":"speed-of-cinnamon","version":2,"settings":{"language":"en\\u0000"},'
                '"alarms":{"version":2,"alarms":[],"last_checked_at":""}}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SettingsExportError, "invalid null byte"):
                read_export(path)

    def test_read_export_rejects_escaped_x00_null_setting_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text(
                '{"app":"speed-of-cinnamon","version":2,"settings":{"language":"en\\\\x00"},'
                '"alarms":{"version":2,"alarms":[],"last_checked_at":""}}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SettingsExportError, "invalid null byte"):
                read_export(path)

    def test_read_export_rejects_oversized_setting_value(self) -> None:
        long_value = "A" * (MAX_SETTINGS_TEXT_CHARS + 10)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text(
                f'{{"app":"speed-of-cinnamon","version":2,"settings":{{"personal-context":"{long_value}"}},'
                f'"alarms":{{"version":2,"alarms":[],"last_checked_at":""}}}}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SettingsExportError, "is too long"):
                read_export(path)

    def test_read_export_truncates_oversized_alarm_list(self) -> None:
        long_alarms = [
            {"id": f"alarm-{index}", "hour": 9, "minute": index % 60, "days": ["mon"], "name": "name"}
            for index in range(MAX_ALARM_COUNT + 5)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            payload = {
                "app": "speed-of-cinnamon",
                "version": 2,
                "created_at": "2026-06-01T09:00:00Z",
                "speed_of_cinnamon_version": "1.0",
                "settings": {"language": "de"},
                "alarms": {"version": 2, "alarms": long_alarms, "last_checked_at": "2026-06-01T09:00:00Z"},
            }
            path.write_text(json.dumps(payload), encoding="utf-8")
            loaded = read_export(path)
        self.assertEqual(len(loaded["alarms"]["alarms"]), MAX_ALARM_COUNT)

    def test_read_export_rejects_null_byte_last_checked_at(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text(
                '{"app":"speed-of-cinnamon","version":2,"settings":{"language":"de"},'
                '"alarms":{"version":2,"alarms":[],"last_checked_at":"2026\\u0000"}}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SettingsExportError, "invalid null byte"):
                read_export(path)

    def test_read_export_rejects_oversized_last_checked_at(self) -> None:
        long_value = "B" * (MAX_SETTINGS_TEXT_CHARS + 10)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text(
                f'{{"app":"speed-of-cinnamon","version":2,"settings":{{"language":"de"}},'
                f'"alarms":{{"version":2,"alarms":[],"last_checked_at":"{long_value}"}}}}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SettingsExportError, "is too long"):
                read_export(path)

    def test_build_export_rejects_oversized_setting_value(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "is too long"):
            build_export({"personal-context": "A" * (MAX_SETTINGS_TEXT_CHARS + 10)})

    def test_normalize_setting_rejects_non_text_boolean_field(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "must be a boolean"):
            normalize_setting("auto-transcribe-timeout", 1)

    def test_normalize_setting_rejects_string_boolean_field(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "must be a boolean"):
            normalize_setting("auto-transcribe-timeout", "true")

    def test_normalize_setting_rejects_boolean_numeric_field(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "must be an integer"):
            normalize_setting("typing-delay-ms", True)

    def test_write_export_rejects_oversized_setting_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            with self.assertRaisesRegex(SettingsExportError, "is too long"):
                write_export(path, {"personal-context": "A" * (MAX_SETTINGS_TEXT_CHARS + 10)})

    def test_read_export_skips_invalid_alarm_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text(
                '{"app":"speed-of-cinnamon","version":2,"settings":{"language":"de"},"alarms":{'
                '"version":2,'
                '"alarms":[{"id":"good","hour":9,"minute":0,"days":["mon"],"name":"Good"},'
                '{"id":"bad","hour":"not-a-number","minute":0,"days":["mon"],"name":"Bad"}],'
                '"last_checked_at":"2026-06-01T09:00"}}',
                encoding="utf-8",
            )
            payload = read_export(path)
        self.assertEqual(len(payload["alarms"]["alarms"]), 1)
        self.assertEqual(payload["alarms"]["alarms"][0]["id"], "good")

    def test_build_export_rejects_null_byte_setting_value(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "invalid null byte"):
            build_export({"language": "en\x00"})

    def test_build_export_rejects_escaped_x00_setting_value(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "invalid null byte"):
            build_export({"language": "en\\x00"})

    def test_write_export_rejects_null_byte_setting_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            with self.assertRaisesRegex(SettingsExportError, "invalid null byte"):
                write_export(path, {"language": "en\x00"})

    def test_write_export_rejects_oversized_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            with mock.patch("speed_of_cinnamon.settings_export.MAX_SETTINGS_EXPORT_BYTES", 1):
                with self.assertRaisesRegex(SettingsExportError, "settings export is too large"):
                    write_export(path, {"language": "en"})

if __name__ == "__main__":
    unittest.main()
