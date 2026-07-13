from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from speed_of_cinnamon.settings_export import (
    MAX_SETTINGS_EXPORT_BYTES,
    MAX_SETTINGS_EXPORT_JSON_DEPTH,
    MAX_SETTINGS_EXPORT_JSON_NODES,
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
from speed_of_cinnamon import settings_export as settings_export_module


class SettingsExportTest(unittest.TestCase):
    def test_write_export_rejects_null_byte_path(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "invalid null byte"):
            write_export(Path("settings\x00.json"), {"language": "en"})

    def test_write_export_rejects_unencodable_path(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "path is invalid"):
            write_export(Path("settings\ud800.json"), {"language": "en"})

    def test_read_export_rejects_null_byte_path(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "invalid null byte"):
            read_export(Path("settings\x00.json"))

    def test_read_export_rejects_unencodable_path(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "path is invalid"):
            read_export(Path("settings\ud800.json"))

    def test_write_export_rejects_control_character_path(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "invalid control character"):
            write_export(Path("settings\x85spoof.json"), {"language": "en"})

    def test_read_export_rejects_escaped_control_character_path(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "invalid control character"):
            read_export(Path("settings\\x85spoof.json"))

    @mock.patch("speed_of_cinnamon.path_safety.os.open", wraps=os.open)
    def test_read_export_uses_secure_open_flags(self, mocked_open: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text('{"app":"speed-of-cinnamon","version":2,"created_at":"","speed_of_cinnamon_version":"",'
                            '"settings":{"language":"en","max-seconds":30},'
                            '"alarms":{"version":2,"alarms":[],"last_checked_at":""}}', encoding="utf-8")
            payload = read_export(path)
        self.assertEqual(payload["app"], "speed-of-cinnamon")
        self.assertTrue(mocked_open.called)
        self.assertTrue(
            any(
                args[0] == path.name
                and isinstance(args[1], int)
                and args[1] & os.O_NOFOLLOW
                and "dir_fd" in kwargs
                for args, kwargs in mocked_open.call_args_list
            )
        )

    def test_read_export_wraps_fdopen_value_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text(json.dumps(build_export({"language": "en"})), encoding="utf-8")
            with mock.patch.object(settings_export_module.os, "fdopen", side_effect=ValueError("bad fd")):
                with self.assertRaisesRegex(SettingsExportError, "settings export could not be read"):
                    read_export(path)

    def test_read_export_rejects_hardlinked_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            hardlink = Path(tmp) / "hardlinked-settings-export.json"
            path.write_text('{"app":"speed-of-cinnamon","version":2,"created_at":"","speed_of_cinnamon_version":"",'
                            '"settings":{"language":"en","max-seconds":30},'
                            '"alarms":{"version":2,"alarms":[],"last_checked_at":""}}', encoding="utf-8")
            try:
                os.link(path, hardlink)
            except OSError as exc:
                self.skipTest(f"hardlinks unavailable: {exc}")
            with self.assertRaisesRegex(SettingsExportError, "must not be hardlinked"):
                read_export(hardlink)

    def test_read_export_rejects_fifo_without_blocking(self) -> None:
        if not hasattr(os, "mkfifo"):
            self.skipTest("fifo creation unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            try:
                os.mkfifo(path)
            except OSError as exc:
                self.skipTest(f"fifo creation unavailable: {exc}")

            with self.assertRaisesRegex(SettingsExportError, "regular file"):
                read_export(path)

    def test_read_export_rejects_escaped_null_path(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "invalid null byte"):
            read_export(Path("settings\\\\x00.json"))

    def test_read_export_rejects_non_path(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "must be a path"):
            read_export("settings-export.json")  # type: ignore[arg-type]

    def test_read_export_rejects_boolean_path(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "must be a path"):
            read_export(True)  # type: ignore[arg-type]

    def test_read_export_rejects_relative_path(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "must be absolute"):
            read_export(Path("settings-export.json"))

    def test_write_export_rejects_non_path(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "must be a path"):
            write_export("settings-export.json", {"language": "en"})  # type: ignore[arg-type]

    def test_write_export_rejects_boolean_path(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "must be a path"):
            write_export(False, {"language": "en"})  # type: ignore[arg-type]

    def test_write_export_rejects_relative_path(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "must be absolute"):
            write_export(Path("settings-export.json"), {"language": "en"})

    def test_sanitize_text_field_rejects_non_text(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "must be text"):
            _sanitize_text_field(1, field_name="setting value")

    def test_sanitize_text_field_rejects_boolean(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "must be text"):
            _sanitize_text_field(True, field_name="setting value")

    def test_sanitize_text_field_rejects_control_character(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "invalid control character"):
            _sanitize_text_field("value\\rextra", field_name="setting value")

    def test_sanitize_text_field_rejects_boundary_control_character(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "invalid control character"):
            _sanitize_text_field("\nvalue", field_name="setting value")
        with self.assertRaisesRegex(SettingsExportError, "invalid control character"):
            _sanitize_text_field("value\r", field_name="setting value")

    def test_sanitize_text_field_rejects_unencodable_text(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "invalid Unicode characters"):
            _sanitize_text_field("value\ud800", field_name="setting value")

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

    def test_read_export_rejects_deeply_nested_json_before_normalization(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text("[" * (MAX_SETTINGS_EXPORT_JSON_DEPTH + 1) + "0" + "]" * (MAX_SETTINGS_EXPORT_JSON_DEPTH + 1), encoding="utf-8")
            with self.assertRaisesRegex(SettingsExportError, "too deeply nested"):
                read_export(path)

    def test_read_export_rejects_overly_complex_json_before_normalization(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            payload = {
                "app": "speed-of-cinnamon",
                "version": 2,
                "settings": {f"k{index}": "" for index in range(MAX_SETTINGS_EXPORT_JSON_NODES + 1)},
                "alarms": {"version": 2, "alarms": [], "last_checked_at": ""},
            }
            path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
            with self.assertRaisesRegex(SettingsExportError, "too complex"):
                read_export(path)

    def test_sanitize_text_field_rejects_oversized_text_bytes(self) -> None:
        with mock.patch("speed_of_cinnamon.settings_export.MAX_SETTINGS_TEXT_CHARS", 4):
            with self.assertRaisesRegex(SettingsExportError, "is too long"):
                _sanitize_text_field("😀" * 2, field_name="setting value")

    def test_normalize_setting_rejects_empty_url_userinfo(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "must not contain URL credentials"):
            normalize_setting("openai-compatible-url", "https://@api.example.test/v1")

    def test_write_export_rejects_oversized_path(self) -> None:
        path = Path("a" * (MAX_SETTINGS_EXPORT_PATH_CHARS + 1))
        with self.assertRaisesRegex(SettingsExportError, "path is invalid"):
            write_export(path, {"language": "en"})

    @mock.patch("speed_of_cinnamon.settings_export.json.dumps", return_value='{"setting":"\ud800"}')
    def test_write_export_rejects_unencodable_rendered_payload(self, mocked_dumps: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            with self.assertRaisesRegex(SettingsExportError, "settings export payload contains invalid Unicode"):
                write_export(path, {"language": "en"})
        mocked_dumps.assert_called_once()

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

    def test_read_export_rejects_control_char_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text(
                '{"app":"speed-of-cinnamon","version":2,"created_at":"2026-06-01\\nspoof",'
                '"speed_of_cinnamon_version":"1.0","settings":{"language":"en","max-seconds":30},'
                '"alarms":{"version":2,"alarms":[],"last_checked_at":""}}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SettingsExportError, "invalid control character"):
                read_export(path)

    def test_read_export_rejects_oversized_metadata(self) -> None:
        long_value = "v" * (MAX_SETTINGS_TEXT_CHARS + 10)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text(
                '{"app":"speed-of-cinnamon","version":2,"created_at":"2026-06-01",'
                f'"speed_of_cinnamon_version":"{long_value}",'
                '"settings":{"language":"en","max-seconds":30},'
                '"alarms":{"version":2,"alarms":[],"last_checked_at":""}}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SettingsExportError, "is too long"):
                read_export(path)

    def test_build_export_keeps_only_supported_settings(self) -> None:
        payload = build_export({
            "primary-language-keybinding": "<Super><Alt>z::",
            "secondary-language-keybinding": "<Super><Shift>z::",
            "cancel-keybinding": "<Super>Escape::",
            "language": "de",
            "show-panel-label": True,
            "append-space": False,
            "auto-transcribe-timeout": False,
            "auto-relisten": True,
            "keep-recording-artifacts": True,
            "sanitize-special-chars": True,
            "soften-profanity": True,
            "typing-delay-ms": "12",
            "max-transcript-files": "500",
            "artifact-encryption": "passphrase",
            "auto-paste-window-title": "Teams",
            "post-process-backend": "ollama",
            "ollama-model": "llama3.2:3b",
            "openai-compatible-url": "http://127.0.0.1:8000/v1",
            "openai-compatible-model": "local-llama",
            "openai-compatible-text-model": "local-polisher",
            "openai-compatible-flex-processing": False,
            "post-process-preset": "code",
            "post-process-preserve-code": True,
            "post-process-never-add-content": True,
            "post-process-mask-sensitive-data": True,
            "cli-path": "/tmp/not-portable",
            "unknown": "ignored",
        })
        settings = payload["settings"]
        self.assertEqual(payload["app"], "speed-of-cinnamon")
        self.assertEqual(settings["primary-language-keybinding"], "<Super><Alt>z::")
        self.assertEqual(settings["secondary-language-keybinding"], "<Super><Shift>z::")
        self.assertEqual(settings["cancel-keybinding"], "<Super>Escape::")
        self.assertEqual(settings["language"], "de")
        self.assertTrue(settings["show-panel-label"])
        self.assertFalse(settings["append-space"])
        self.assertFalse(settings["auto-transcribe-timeout"])
        self.assertTrue(settings["auto-relisten"])
        self.assertTrue(settings["keep-recording-artifacts"])
        self.assertTrue(settings["sanitize-special-chars"])
        self.assertTrue(settings["soften-profanity"])
        self.assertEqual(settings["typing-delay-ms"], 12)
        self.assertEqual(settings["max-transcript-files"], 500)
        self.assertEqual(settings["artifact-encryption"], "passphrase")
        self.assertEqual(settings["auto-paste-window-title"], "Teams")
        self.assertEqual(settings["post-process-backend"], "ollama")
        self.assertEqual(settings["ollama-model"], "llama3.2:3b")
        self.assertEqual(settings["openai-compatible-url"], "http://127.0.0.1:8000/v1")
        self.assertEqual(settings["openai-compatible-model"], "local-llama")
        self.assertEqual(settings["openai-compatible-text-model"], "local-polisher")
        self.assertFalse(settings["openai-compatible-flex-processing"])
        self.assertEqual(settings["post-process-preset"], "code")
        self.assertTrue(settings["post-process-preserve-code"])
        self.assertTrue(settings["post-process-never-add-content"])
        self.assertTrue(settings["post-process-mask-sensitive-data"])
        self.assertNotIn("cli-path", settings)
        self.assertNotIn("unknown", settings)

    def test_build_export_does_not_leak_command_settings(self) -> None:
        payload = build_export({
            "openai-compatible-api-key": "sk-live-secret",
            "transcriber-command": "custom-asr --token sk-secret-token",
            "post-process-command": "polish --api-key ghp_secret",
            "language": "de",
        })
        rendered = json.dumps(payload, sort_keys=True)

        self.assertNotIn("openai-compatible-api-key", payload["settings"])
        self.assertNotIn("transcriber-command", payload["settings"])
        self.assertNotIn("post-process-command", payload["settings"])
        self.assertIn("openai-compatible-api-key", payload["excluded_private_settings"])
        self.assertIn("transcriber-command", payload["excluded_private_settings"])
        self.assertIn("post-process-command", payload["excluded_private_settings"])
        self.assertIn("cli-path", payload["excluded_private_settings"])
        self.assertNotIn("sk-live-secret", rendered)
        self.assertNotIn("sk-secret-token", rendered)
        self.assertNotIn("ghp_secret", rendered)

    def test_build_export_rejects_secret_bearing_backend_urls(self) -> None:
        docs = [
            Path("docs/user-guide.md").read_text(encoding="utf-8"),
            Path("docs/cli-reference.md").read_text(encoding="utf-8"),
            Path("docs/fedora-cinnamon-runbook.md").read_text(encoding="utf-8"),
        ]
        for text in docs:
            self.assertIn("Backend URLs with embedded credentials", text)
            self.assertIn("fragments are rejected", text)
        with self.assertRaisesRegex(SettingsExportError, "openai-compatible-url must not contain URL credentials"):
            build_export({"openai-compatible-url": "https://user:secret-token@api.example.test/v1"})
        with self.assertRaisesRegex(SettingsExportError, "openai-compatible-url must not contain URL query or fragment"):
            build_export({"openai-compatible-url": "https://api.example.test/v1?api_key=secret-token"})
        with self.assertRaisesRegex(SettingsExportError, "ollama-url must not contain URL query or fragment"):
            build_export({"ollama-url": "http://127.0.0.1:11434#secret-token"})
        with self.assertRaisesRegex(SettingsExportError, "openai-compatible-url must use https:// unless host is local loopback"):
            build_export({"openai-compatible-url": "http://api.example.test/v1"})
        with self.assertRaisesRegex(SettingsExportError, "ollama-url must use https:// unless host is local loopback"):
            build_export({"ollama-url": "http://api.example.test:11434"})
        with self.assertRaisesRegex(SettingsExportError, "openai-compatible-url must use http:// or https://"):
            build_export({"openai-compatible-url": "ftp://127.0.0.1:8000/v1"})
        with self.assertRaisesRegex(SettingsExportError, "openai-compatible-url has invalid port"):
            build_export({"openai-compatible-url": "https://api.example.test:bad/v1"})

    def test_build_export_rejects_unknown_mode_values(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "setting insert-method has unsupported value"):
            build_export({"insert-method": "clipboard-paste\x20--unsafe"})
        with self.assertRaisesRegex(SettingsExportError, "setting artifact-encryption has unsupported value"):
            build_export({"artifact-encryption": "plaintext"})
        with self.assertRaisesRegex(SettingsExportError, "setting post-process-backend has unsupported value"):
            build_export({"post-process-backend": "remote-shell"})

    def test_write_and_read_export_round_trips_normalized_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            write_export(
                path,
                {
                    "auto-transcribe-timeout": False,
                    "auto-relisten": True,
                    "auto-paste-window-title": "Teams",
                    "max-transcript-files": 500,
                    "artifact-encryption": "keyring",
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
        self.assertTrue(payload["settings"]["auto-relisten"])
        self.assertEqual(payload["settings"]["auto-paste-window-title"], "Teams")
        self.assertEqual(payload["settings"]["max-transcript-files"], 500)
        self.assertEqual(payload["settings"]["artifact-encryption"], "keyring")
        self.assertFalse(payload["settings"]["notify-complete"])
        self.assertEqual(payload["settings"]["personal-context"], "Project words")
        self.assertIn("openai-compatible-api-key", payload["excluded_private_settings"])
        self.assertIn("transcriber-command", payload["excluded_private_settings"])
        self.assertEqual(payload["alarms"]["last_checked_at"], "2026-06-01T09:10")
        self.assertEqual(payload["alarms"]["alarms"][0]["name"], "Standup")
        self.assertEqual(payload["alarms"]["alarms"][0]["days"], ["mon", "wed", "fri"])

    def test_read_export_uses_schema_aligned_show_panel_label_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text(
                '{"app":"speed-of-cinnamon","version":2,"settings":{"language":"de"},'
                '"alarms":{"version":2,"alarms":[],"last_checked_at":""}}',
                encoding="utf-8",
            )
            payload = read_export(path)

        self.assertFalse(payload["settings"]["show-panel-label"])

    def test_write_export_rejects_out_of_range_numeric_settings(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "must be at least"):
            build_export({"max-seconds": -2})
        with self.assertRaisesRegex(SettingsExportError, "must be at most"):
            build_export({"typing-delay-ms": MAX_TYPING_DELAY_MS + 1})
        with self.assertRaisesRegex(SettingsExportError, "max-transcript-files must be at least"):
            build_export({"max-transcript-files": 0})
        with self.assertRaisesRegex(SettingsExportError, "max-transcript-files must be at most"):
            build_export({"max-transcript-files": 1001})

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
                '{"app":"speed-of-cinnamon","version":2,"settings":{"max-transcript-files":0},'
                '"alarms":{"version":2,"alarms":[],"last_checked_at":""}}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SettingsExportError, "max-transcript-files must be at least"):
                read_export(path)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text(
                '{"app":"speed-of-cinnamon","version":2,"settings":{"max-transcript-files":1001},'
                '"alarms":{"version":2,"alarms":[],"last_checked_at":""}}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SettingsExportError, "max-transcript-files must be at most"):
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

    def test_read_export_rejects_unknown_mode_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text(
                '{"app":"speed-of-cinnamon","version":2,"settings":{"transcriber":"shell"},'
                '"alarms":{"version":2,"alarms":[],"last_checked_at":""}}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SettingsExportError, "setting transcriber has unsupported value"):
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

    def test_read_export_rejects_control_char_app_without_echoing_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text('{"app":"evil\\u001b[31m","version":1,"settings":{}}\n', encoding="utf-8")
            with self.assertRaisesRegex(SettingsExportError, "different app") as ctx:
                read_export(path)
        self.assertNotIn("evil", str(ctx.exception))

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

    def test_write_export_reports_temp_cleanup_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            with (
                mock.patch("speed_of_cinnamon.settings_export.os.replace", side_effect=OSError("disk full")),
                mock.patch("speed_of_cinnamon.settings_export.os.unlink", side_effect=OSError("cleanup denied")),
            ):
                with self.assertRaisesRegex(SettingsExportError, "failed to remove settings export temporary file"):
                    write_export(path, {"language": "en"})

            self.assertFalse(path.exists())
            leftovers = [child for child in Path(tmp).iterdir() if child.name.startswith(".settings-export.json.") and child.name.endswith(".tmp")]
            self.assertEqual(len(leftovers), 1)
            self.assertEqual(leftovers[0].read_bytes(), b"")

    @mock.patch("speed_of_cinnamon.settings_export.os.replace", side_effect=OSError("disk full"))
    def test_write_export_raises_when_atomic_replace_fails(self, mocked_replace: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            with self.assertRaisesRegex(SettingsExportError, "failed to write settings export"):
                write_export(path, {"language": "en"})
        mocked_replace.assert_called_once()

    def test_write_export_closes_temporary_fd_when_fdopen_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            created_fds: list[int] = []
            real_create = settings_export_module._create_private_temp_file

            def create_temp_file(parent_fd: int, final_name: str) -> tuple[int, str]:
                fd, temp_name = real_create(parent_fd, final_name)
                created_fds.append(fd)
                return fd, temp_name

            with (
                mock.patch.object(settings_export_module, "_create_private_temp_file", side_effect=create_temp_file),
                mock.patch.object(settings_export_module.os, "fdopen", side_effect=OSError("fdopen failed")),
            ):
                with self.assertRaisesRegex(SettingsExportError, "failed to write settings export"):
                    write_export(path, {"language": "en"})

            self.assertEqual(len(created_fds), 1)
            with self.assertRaises(OSError):
                os.fstat(created_fds[0])

    def test_write_export_rejects_leaf_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target.json"
            target.write_text("keep\n", encoding="utf-8")
            path = Path(tmp) / "settings-export.json"
            path.symlink_to(target)

            with self.assertRaisesRegex(SettingsExportError, "must not pass through a symlink"):
                write_export(path, {"language": "en"})

            self.assertTrue(path.is_symlink())
            self.assertEqual(target.read_text(encoding="utf-8"), "keep\n")

    def test_write_export_fsyncs_temp_file_and_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            with mock.patch("speed_of_cinnamon.settings_export.os.fsync", wraps=os.fsync) as mocked_fsync:
                write_export(path, {"language": "en"})

            self.assertTrue(path.exists())
            self.assertGreaterEqual(mocked_fsync.call_count, 2)

    def test_write_export_removes_temp_file_when_file_fsync_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            fsynced_modes: list[int] = []
            real_fsync = os.fsync

            def failing_file_fsync(fd: int) -> None:
                mode = os.fstat(fd).st_mode
                fsynced_modes.append(mode)
                if stat.S_ISREG(mode):
                    raise OSError("sync failed")
                real_fsync(fd)

            with mock.patch("speed_of_cinnamon.settings_export.os.fsync", side_effect=failing_file_fsync):
                with self.assertRaisesRegex(SettingsExportError, "failed to write settings export"):
                    write_export(path, {"language": "en"})

            self.assertFalse(path.exists())
            self.assertEqual(list(Path(tmp).iterdir()), [])
            self.assertTrue(any(stat.S_ISDIR(mode) for mode in fsynced_modes))

    @mock.patch("speed_of_cinnamon.path_safety.os.open", wraps=os.open)
    def test_write_export_uses_secure_parent_directory_open(self, mocked_open: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            write_export(path, {"language": "en"})

        self.assertTrue(
            any(
                args[0] == path.parent.name
                and isinstance(args[1], int)
                and args[1] & os.O_NOFOLLOW
                and "dir_fd" in kwargs
                for args, kwargs in mocked_open.call_args_list
            )
        )

    def test_write_export_creates_parent_without_pathlib_mkdir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "settings-export.json"
            with mock.patch.object(Path, "mkdir", side_effect=AssertionError("unsafe mkdir")):
                write_export(path, {"language": "en"})

            self.assertTrue(path.exists())

    def test_write_export_rejects_non_private_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.chmod(tmp, 0o777)
            path = Path(tmp) / "settings-export.json"
            with self.assertRaisesRegex(SettingsExportError, "settings export directory must be private"):
                write_export(path, {"language": "en"})

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

    def test_normalize_setting_rejects_null_text_field(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "must be text"):
            normalize_setting("language", None)

    def test_build_export_rejects_null_text_setting_value(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "must be text"):
            build_export({"language": None})

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
