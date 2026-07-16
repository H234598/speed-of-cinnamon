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
    MAX_SETTINGS_URL_CHARS,
    MAX_OLLAMA_MODEL_CHARS,
    MAX_OPENAI_COMPATIBLE_MODEL_CHARS,
    MAX_RECORDING_INPUT_DEVICE_CHARS,
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

        long_url = "http://127.0.0.1:11434/" + ("x" * MAX_SETTINGS_URL_CHARS)
        with self.assertRaisesRegex(SettingsExportError, "setting ollama-url is too long"):
            build_export({"ollama-url": long_url})

    def test_build_export_rejects_runtime_oversized_text_settings(self) -> None:
        limits = {
            "input-device": MAX_RECORDING_INPUT_DEVICE_CHARS,
            "ollama-model": MAX_OLLAMA_MODEL_CHARS,
            "openai-compatible-model": MAX_OPENAI_COMPATIBLE_MODEL_CHARS,
            "openai-compatible-text-model": MAX_OPENAI_COMPATIBLE_MODEL_CHARS,
        }
        for key, limit in limits.items():
            with self.subTest(key=key):
                with self.assertRaisesRegex(SettingsExportError, rf"setting {key} is too long"):
                    build_export({key: "x" * (limit + 1)})

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

    def test_normalize_alarm_store_assigns_unique_alarm_ids(self) -> None:
        payload = normalize_alarm_store({
            "alarms": [
                {"id": "meeting", "hour": 9, "minute": 0, "days": ["mon"]},
                {"id": "meeting", "hour": 10, "minute": 0, "days": ["mon"]},
            ],
        })

        self.assertEqual([alarm["id"] for alarm in payload["alarms"]], ["meeting", "meeting-2"])

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

    def test_write_export_preserves_primary_error_when_temp_cleanup_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            with (
                mock.patch("speed_of_cinnamon.settings_export._rename_without_replacing", side_effect=OSError("disk full")),
                mock.patch("speed_of_cinnamon.settings_export.os.unlink", side_effect=OSError("cleanup denied")),
            ):
                with self.assertRaisesRegex(SettingsExportError, "failed to write settings export") as caught:
                    write_export(path, {"language": "en"})

            self.assertFalse(path.exists())
            leftovers = [child for child in Path(tmp).iterdir() if child.name.startswith(".settings-export.json.") and child.name.endswith(".tmp")]
            self.assertEqual(len(leftovers), 1)
            self.assertEqual(leftovers[0].read_bytes(), b"")
        self.assertIsNotNone(caught.exception.__cause__)
        self.assertIn("disk full", str(caught.exception.__cause__))
        self.assertIn("settings export cleanup failed", "\n".join(caught.exception.__notes__))
        self.assertIn("cleanup denied", "\n".join(caught.exception.__notes__))

    def test_write_export_preserves_primary_error_when_temp_cleanup_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            with (
                mock.patch.object(
                    settings_export_module,
                    "_rename_without_replacing",
                    side_effect=RuntimeError("activation failed"),
                ),
                mock.patch.object(settings_export_module.os, "unlink", side_effect=KeyboardInterrupt),
            ):
                with self.assertRaisesRegex(RuntimeError, "activation failed") as caught:
                    write_export(path, {"language": "en"})

            self.assertIn("settings export cleanup failed", "\n".join(caught.exception.__notes__))

    @mock.patch("speed_of_cinnamon.settings_export._rename_without_replacing", side_effect=OSError("disk full"))
    def test_write_export_raises_when_atomic_activation_fails(self, mocked_rename: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            with self.assertRaisesRegex(SettingsExportError, "failed to write settings export"):
                write_export(path, {"language": "en"})
        mocked_rename.assert_called_once()

    def test_write_export_does_not_clobber_target_created_during_activation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            replacement = Path(tmp) / "replacement.json"
            replacement.write_text("racing target", encoding="utf-8")
            real_rename = settings_export_module._rename_without_replacing

            def rename_then_create_target(
                source: str,
                target: str,
                *,
                directory_fd: int,
                field_name: str,
            ) -> None:
                if target == path.name:
                    replacement.replace(path)
                real_rename(source, target, directory_fd=directory_fd, field_name=field_name)

            with mock.patch.object(
                settings_export_module,
                "_rename_without_replacing",
                side_effect=rename_then_create_target,
            ):
                with self.assertRaisesRegex(SettingsExportError, "failed to write settings export"):
                    write_export(path, {"language": "de"})

            self.assertEqual(path.read_text(encoding="utf-8"), "racing target")
            self.assertFalse(list(Path(tmp).glob(".settings-export.json.*.tmp")))

    def test_create_private_temp_file_rejects_missing_nofollow(self) -> None:
        with (
            mock.patch.object(settings_export_module.os, "O_NOFOLLOW", None, create=True),
            self.assertRaisesRegex(SettingsExportError, "secure settings export temp file creation"),
        ):
            settings_export_module._create_private_temp_file(456, "settings-export.json")

    def test_write_export_does_not_overwrite_existing_recovery_backup_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text("old export\n", encoding="utf-8")
            racing_candidate = Path(tmp) / ".settings-export.json.fixed.bak"
            racing_candidate.write_text("racing backup\n", encoding="utf-8")

            with mock.patch.object(
                settings_export_module.secrets,
                "token_hex",
                side_effect=["temp", "fixed", "free"],
            ):
                write_export(path, {"language": "de"})

            self.assertEqual(racing_candidate.read_text(encoding="utf-8"), "racing backup\n")
            self.assertFalse((Path(tmp) / ".settings-export.json.free.bak").exists())
            self.assertEqual(read_export(path)["settings"]["language"], "de")

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

    def test_write_export_closes_and_removes_temp_when_fdopen_is_interrupted(self) -> None:
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
                mock.patch.object(settings_export_module.os, "fdopen", side_effect=KeyboardInterrupt),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    write_export(path, {"language": "en"})

            self.assertEqual(len(created_fds), 1)
            with self.assertRaises(OSError):
                os.fstat(created_fds[0])
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_write_export_does_not_write_when_initial_temp_identity_inspection_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            real_fstat = os.fstat
            failed_regular_fstats = 0

            def fail_once_for_temp_file(fd: int) -> os.stat_result:
                nonlocal failed_regular_fstats
                result = real_fstat(fd)
                if stat.S_ISREG(result.st_mode) and failed_regular_fstats == 0:
                    failed_regular_fstats += 1
                    raise OSError("temp identity inspection failed")
                return result

            with mock.patch.object(settings_export_module.os, "fstat", side_effect=fail_once_for_temp_file):
                with self.assertRaisesRegex(SettingsExportError, "failed to write settings export"):
                    write_export(path, {"language": "de", "personal-context": "SECRET"})

            self.assertEqual(failed_regular_fstats, 1)
            self.assertFalse(path.exists())
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_write_export_does_not_remove_replaced_temp_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            temp_path: Path | None = None
            temp_fd: int | None = None
            real_create = settings_export_module._create_private_temp_file

            def create_temp_file(parent_fd: int, final_name: str) -> tuple[int, str]:
                nonlocal temp_path, temp_fd
                temp_fd, temp_name = real_create(parent_fd, final_name)
                temp_path = Path(tmp) / temp_name
                return temp_fd, temp_name

            class _Handle:
                def fileno(self) -> int:
                    assert temp_fd is not None
                    return temp_fd

                def write(self, _payload: str) -> int:
                    assert temp_path is not None
                    replacement = Path(tmp) / "replacement.json"
                    replacement.write_bytes(b"replacement")
                    os.replace(replacement, temp_path)
                    raise OSError("write failed")

                def flush(self) -> None:
                    return None

                def close(self) -> None:
                    if temp_fd is not None:
                        os.close(temp_fd)

            with (
                mock.patch.object(settings_export_module, "_create_private_temp_file", side_effect=create_temp_file),
                mock.patch.object(settings_export_module.os, "fdopen", return_value=_Handle()),
            ):
                with self.assertRaisesRegex(SettingsExportError, "failed to write settings export"):
                    write_export(path, {"language": "en"})

            self.assertIsNotNone(temp_path)
            self.assertEqual(temp_path.read_bytes(), b"replacement")

    def test_write_export_preserves_fdopen_error_when_temp_fd_close_is_interrupted(self) -> None:
        def close(fd: int) -> None:
            if fd == 123:
                raise KeyboardInterrupt

        with (
            mock.patch.object(settings_export_module, "ensure_directory_without_following_symlinks", return_value=456),
            mock.patch.object(settings_export_module, "assert_fd_is_private_directory"),
            mock.patch.object(settings_export_module.os, "fstat", return_value=mock.Mock()),
            mock.patch.object(settings_export_module.os, "stat", side_effect=FileNotFoundError),
            mock.patch.object(
                settings_export_module,
                "_create_private_temp_file",
                return_value=(123, ".settings-export.json.tmp"),
            ),
            mock.patch.object(settings_export_module.os, "fdopen", side_effect=RuntimeError("fdopen failed")),
            mock.patch.object(settings_export_module.os, "unlink"),
            mock.patch.object(settings_export_module.os, "close", side_effect=close),
        ):
            with self.assertRaisesRegex(RuntimeError, "fdopen failed") as caught:
                write_export(Path("/probe/settings-export.json"), {"language": "en"})

        self.assertIn("settings export cleanup failed", "\n".join(caught.exception.__notes__))

    def test_write_export_preserves_fdopen_error_when_temp_fd_close_fails(self) -> None:
        def close(fd: int) -> None:
            if fd == 123:
                raise OSError("temp close failed")

        with (
            mock.patch.object(settings_export_module, "ensure_directory_without_following_symlinks", return_value=456),
            mock.patch.object(settings_export_module, "assert_fd_is_private_directory"),
            mock.patch.object(settings_export_module.os, "fstat", return_value=mock.Mock()),
            mock.patch.object(settings_export_module.os, "stat", side_effect=FileNotFoundError),
            mock.patch.object(
                settings_export_module,
                "_create_private_temp_file",
                return_value=(123, ".settings-export.json.tmp"),
            ),
            mock.patch.object(settings_export_module.os, "fdopen", side_effect=RuntimeError("fdopen failed")),
            mock.patch.object(settings_export_module.os, "unlink"),
            mock.patch.object(settings_export_module.os, "fsync"),
            mock.patch.object(settings_export_module.os, "close", side_effect=close),
        ):
            with self.assertRaisesRegex(RuntimeError, "fdopen failed") as caught:
                write_export(Path("/probe/settings-export.json"), {"language": "en"})

        self.assertIn("settings export cleanup failed", "\n".join(caught.exception.__notes__))
        self.assertIn("temp close failed", "\n".join(caught.exception.__notes__))

    def test_write_export_preserves_write_error_when_temporary_handle_close_fails(self) -> None:
        class _Handle:
            def fileno(self) -> int:
                return 123

            def write(self, _payload: str) -> int:
                raise OSError("write failed")

            def flush(self) -> None:
                return None

            def close(self) -> None:
                raise OSError("close failed")

        with (
            mock.patch.object(settings_export_module, "ensure_directory_without_following_symlinks", return_value=456),
            mock.patch.object(settings_export_module, "assert_fd_is_private_directory"),
            mock.patch.object(settings_export_module.os, "fstat", return_value=mock.Mock()),
            mock.patch.object(settings_export_module.os, "stat", side_effect=FileNotFoundError),
            mock.patch.object(
                settings_export_module,
                "_create_private_temp_file",
                return_value=(123, ".settings-export.json.tmp"),
            ),
            mock.patch.object(settings_export_module.os, "fdopen", return_value=_Handle()),
            mock.patch.object(settings_export_module.os, "fchmod"),
            mock.patch.object(settings_export_module.os, "unlink"),
            mock.patch.object(settings_export_module.os, "fsync"),
            mock.patch.object(settings_export_module.os, "close"),
        ):
            with self.assertRaisesRegex(SettingsExportError, "failed to write settings export") as caught:
                write_export(Path("/probe/settings-export.json"), {"language": "en"})

        self.assertIsNotNone(caught.exception.__cause__)
        self.assertIn("write failed", str(caught.exception.__cause__))
        self.assertIn("settings export cleanup failed", "\n".join(caught.exception.__notes__))
        self.assertIn("close failed", "\n".join(caught.exception.__notes__))

    def test_read_export_preserves_fd_validation_error_when_fd_close_fails(self) -> None:
        with (
            mock.patch.object(settings_export_module, "open_file_without_following_symlinks", return_value=123),
            mock.patch.object(
                settings_export_module,
                "assert_fd_is_regular_private_file",
                side_effect=RuntimeError("not private"),
            ),
            mock.patch.object(settings_export_module.os, "close", side_effect=OSError("close failed")),
        ):
            with self.assertRaisesRegex(SettingsExportError, "not private") as caught:
                settings_export_module._read_text_capped_without_following_symlinks(Path("/settings.json"))

        self.assertIn("settings export cleanup failed", "\n".join(caught.exception.__notes__))

    def test_read_export_preserves_read_interrupt_when_handle_close_fails(self) -> None:
        class _Handle:
            def read(self, _size: int = -1) -> str:
                raise KeyboardInterrupt("read interrupted")

            def close(self) -> None:
                raise OSError("close failed")

        with (
            mock.patch.object(settings_export_module, "open_file_without_following_symlinks", return_value=123),
            mock.patch.object(settings_export_module, "assert_fd_is_regular_private_file"),
            mock.patch.object(settings_export_module.os, "fstat", return_value=mock.Mock(st_size=0)),
            mock.patch.object(settings_export_module.os, "fdopen", return_value=_Handle()),
        ):
            with self.assertRaisesRegex(KeyboardInterrupt, "read interrupted") as caught:
                settings_export_module._read_text_capped_without_following_symlinks(Path("/settings.json"))

        self.assertIn("settings export cleanup failed", "\n".join(caught.exception.__notes__))
        self.assertIn("close failed", "\n".join(caught.exception.__notes__))

    def test_read_export_preserves_fd_validation_error_when_fd_close_is_interrupted(self) -> None:
        with (
            mock.patch.object(settings_export_module, "open_file_without_following_symlinks", return_value=123),
            mock.patch.object(
                settings_export_module,
                "assert_fd_is_regular_private_file",
                side_effect=RuntimeError("not private"),
            ),
            mock.patch.object(settings_export_module.os, "close", side_effect=KeyboardInterrupt),
        ):
            with self.assertRaisesRegex(SettingsExportError, "not private") as caught:
                settings_export_module._read_text_capped_without_following_symlinks(Path("/settings.json"))

        self.assertIn("settings export cleanup failed", "\n".join(caught.exception.__notes__))

    def test_scrub_preserves_inspection_error_when_fd_close_fails(self) -> None:
        with (
            mock.patch.object(settings_export_module.os, "open", return_value=123),
            mock.patch.object(settings_export_module.os, "fstat", side_effect=OSError("inspect failed")),
            mock.patch.object(settings_export_module.os, "close", side_effect=OSError("close failed")),
        ):
            with self.assertRaisesRegex(OSError, "inspect failed") as caught:
                settings_export_module._scrub_temp_settings_export_file(456, ".settings.tmp")

        self.assertIn("settings export cleanup failed", "\n".join(caught.exception.__notes__))

    def test_scrub_preserves_inspection_error_when_fd_close_is_interrupted(self) -> None:
        with (
            mock.patch.object(settings_export_module.os, "open", return_value=123),
            mock.patch.object(settings_export_module.os, "fstat", side_effect=RuntimeError("inspect failed")),
            mock.patch.object(settings_export_module.os, "close", side_effect=KeyboardInterrupt),
        ):
            with self.assertRaisesRegex(RuntimeError, "inspect failed") as caught:
                settings_export_module._scrub_temp_settings_export_file(456, ".settings.tmp")

        self.assertIn("settings export cleanup failed", "\n".join(caught.exception.__notes__))

    def test_scrub_temp_file_rejects_missing_nofollow(self) -> None:
        with (
            mock.patch.object(settings_export_module.os, "O_NOFOLLOW", None, create=True),
            self.assertRaisesRegex(SettingsExportError, "secure settings export temp file scrubbing"),
        ):
            settings_export_module._scrub_temp_settings_export_file(456, ".settings.tmp")

    def test_scrub_temp_file_opens_nonblocking(self) -> None:
        with (
            mock.patch.object(settings_export_module.os, "open", return_value=123) as mocked_open,
            mock.patch.object(
                settings_export_module.os,
                "fstat",
                return_value=mock.Mock(st_mode=stat.S_IFREG, st_size=0),
            ),
            mock.patch.object(settings_export_module.os, "ftruncate"),
            mock.patch.object(settings_export_module.os, "close"),
        ):
            settings_export_module._scrub_temp_settings_export_file(456, ".settings.tmp")

        flags = mocked_open.call_args.args[1]
        self.assertTrue(flags & getattr(os, "O_NONBLOCK", 0))

    def test_write_export_reports_parent_close_failure_after_successful_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            real_ensure = settings_export_module.ensure_directory_without_following_symlinks
            real_close = os.close
            parent_fds: list[int] = []

            def ensure_parent(directory: Path, *, field_name: str) -> int:
                fd = real_ensure(directory, field_name=field_name)
                parent_fds.append(fd)
                return fd

            def close_wrapper(fd: int) -> None:
                if fd in parent_fds:
                    raise OSError("parent close failed")
                real_close(fd)

            with (
                mock.patch.object(settings_export_module, "ensure_directory_without_following_symlinks", side_effect=ensure_parent),
                mock.patch.object(settings_export_module.os, "close", side_effect=close_wrapper),
            ):
                with self.assertRaisesRegex(SettingsExportError, "failed to close settings export directory"):
                    write_export(path, {"language": "en"})

            self.assertTrue(path.exists())

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

    def test_write_export_preserves_success_when_recovery_backup_cleanup_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text("old export\n", encoding="utf-8")
            real_unlink = os.unlink

            def fail_backup_cleanup(name: object, *args: object, **kwargs: object) -> None:
                if isinstance(name, str) and name.endswith(".bak"):
                    raise OSError("backup cleanup failed")
                real_unlink(name, *args, **kwargs)

            with mock.patch.object(settings_export_module.os, "unlink", side_effect=fail_backup_cleanup):
                write_export(path, {"language": "de"})

            self.assertEqual(read_export(path)["settings"]["language"], "de")
            self.assertEqual(len(list(Path(tmp).glob(".settings-export.json.*.bak"))), 1)

    def test_write_export_preserves_success_when_recovery_backup_cleanup_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text("old export\n", encoding="utf-8")
            real_unlink = os.unlink

            def interrupt_backup_cleanup(name: object, *args: object, **kwargs: object) -> None:
                if isinstance(name, str) and name.endswith(".bak"):
                    raise KeyboardInterrupt("backup cleanup interrupted")
                real_unlink(name, *args, **kwargs)

            with mock.patch.object(settings_export_module.os, "unlink", side_effect=interrupt_backup_cleanup):
                write_export(path, {"language": "de"})

            self.assertEqual(read_export(path)["settings"]["language"], "de")
            self.assertEqual(len(list(Path(tmp).glob(".settings-export.json.*.bak"))), 1)

    def test_write_export_rolls_back_after_activation_parent_fsync_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text("old export\n", encoding="utf-8")
            path.chmod(0o600)
            real_fsync = os.fsync
            directory_syncs = 0

            def fail_activation_sync(fd: int) -> None:
                nonlocal directory_syncs
                mode = os.fstat(fd).st_mode
                if stat.S_ISDIR(mode):
                    directory_syncs += 1
                    if directory_syncs == 2:
                        raise OSError("activation directory sync failed")
                real_fsync(fd)

            with mock.patch.object(settings_export_module.os, "fsync", side_effect=fail_activation_sync):
                with self.assertRaisesRegex(SettingsExportError, "failed to write settings export"):
                    write_export(path, {"language": "de"})

            self.assertEqual(path.read_text(encoding="utf-8"), "old export\n")
            leftovers = [child for child in Path(tmp).iterdir() if child.name.startswith(".settings-export.json.")]
            self.assertEqual(leftovers, [])

    def test_write_export_restores_target_when_backup_unlink_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text("old export\n", encoding="utf-8")
            path.chmod(0o600)
            real_unlink = os.unlink
            interrupted = False

            def unlink_then_interrupt(name: object, *args: object, **kwargs: object) -> None:
                nonlocal interrupted
                if name == path.name and not interrupted:
                    interrupted = True
                    real_unlink(name, *args, **kwargs)
                    raise KeyboardInterrupt
                real_unlink(name, *args, **kwargs)

            with mock.patch.object(settings_export_module.os, "unlink", side_effect=unlink_then_interrupt):
                with self.assertRaises(KeyboardInterrupt):
                    write_export(path, {"language": "de"})

            self.assertTrue(interrupted)
            self.assertEqual(path.read_text(encoding="utf-8"), "old export\n")
            self.assertFalse(list(Path(tmp).glob(".settings-export.json.*.bak")))
            self.assertFalse(list(Path(tmp).glob(".settings-export.json.*.tmp")))

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
