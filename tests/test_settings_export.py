from __future__ import annotations

import json
import os
import re
import stat
import tempfile
import unittest
import warnings
from contextlib import contextmanager
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
    build_export,
    read_export,
    write_export,
)
from speed_of_cinnamon import settings_export as settings_export_module


class SettingsExportTest(unittest.TestCase):
    @contextmanager
    def _runtime_warning(self, pattern: str):
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always", RuntimeWarning)
            yield captured
        self.assertTrue(
            any(
                issubclass(item.category, RuntimeWarning) and re.search(pattern, str(item.message))
                for item in captured
            ),
            f"expected RuntimeWarning matching {pattern!r}",
        )

    def test_build_export_rejects_non_object_settings(self) -> None:
        for settings in (None, [], "bad", 1):
            with self.subTest(settings=settings):
                with self.assertRaisesRegex(SettingsExportError, "settings export settings must be an object"):
                    build_export(settings)  # type: ignore[arg-type]

    def test_fsync_retries_interrupted_calls(self) -> None:
        with mock.patch.object(
            settings_export_module.os,
            "fsync",
            side_effect=[InterruptedError(), None],
        ) as mocked_fsync:
            settings_export_module._fsync_fd(123)

        self.assertEqual(mocked_fsync.call_args_list, [mock.call(123), mock.call(123)])

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

    def test_read_export_wraps_json_memory_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text("{}", encoding="utf-8")
            with (
                mock.patch.object(settings_export_module.json, "loads", side_effect=MemoryError("too large")),
                self.assertRaisesRegex(SettingsExportError, "could not be read"),
            ):
                read_export(path)

    def test_write_export_wraps_json_memory_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            with (
                mock.patch.object(settings_export_module.json, "dumps", side_effect=MemoryError("too large")),
                self.assertRaisesRegex(SettingsExportError, "could not be rendered"),
            ):
                write_export(path, {"language": "en"})

    def test_write_export_wraps_json_recursion_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            with (
                mock.patch.object(settings_export_module.json, "dumps", side_effect=RecursionError("too deep")),
                self.assertRaisesRegex(SettingsExportError, "could not be rendered"),
            ):
                write_export(path, {"language": "en"})

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

    def test_read_export_rejects_non_finite_numbers(self) -> None:
        for literal in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(literal=literal):
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / "settings-export.json"
                    path.write_text(
                        '{"app":"speed-of-cinnamon","version":2,'
                        f'"future-field":{literal},'
                        '"settings":{"language":"en"},'
                        '"alarms":{"version":2,"alarms":[],"last_checked_at":""}}',
                        encoding="utf-8",
                    )
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

    def test_build_export_preserves_transcript_visibility_and_status_icons(self) -> None:
        settings = build_export({
            "show-transcript-text": False,
            "status-icon-ready": "ready-01",
            "status-icon-recording": "recording-51",
            "status-icon-processing": "processing-12",
            "status-icon-recorded": "soc-original",
            "status-icon-error": "error-03",
            "status-icon-setup": "setup-20",
        })["settings"]

        self.assertFalse(settings["show-transcript-text"])
        self.assertEqual(settings["status-icon-ready"], "ready-01")
        self.assertEqual(settings["status-icon-recording"], "recording-51")
        self.assertEqual(settings["status-icon-processing"], "processing-12")
        self.assertEqual(settings["status-icon-recorded"], "soc-original")
        self.assertEqual(settings["status-icon-error"], "error-03")
        self.assertEqual(settings["status-icon-setup"], "setup-20")

    def test_build_export_rejects_unknown_status_icon(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "setting status-icon-ready has unsupported value"):
            build_export({"status-icon-ready": "ready-999"})

    def test_build_export_does_not_leak_command_settings(self) -> None:
        payload = build_export({
            "openai-compatible-api-key": "sk-live-secret",
            "personal-context": "PRIVATE PERSONAL CONTEXT",
            "transcriber-command": "custom-asr --token sk-secret-token",
            "post-process-command": "polish --api-key ghp_secret",
            "post-process-prompt": "PRIVATE POST PROCESS PROMPT",
            "vocabulary": "PRIVATE VOCABULARY",
            "language": "de",
        })
        rendered = json.dumps(payload, sort_keys=True)

        self.assertNotIn("openai-compatible-api-key", payload["settings"])
        self.assertNotIn("transcriber-command", payload["settings"])
        self.assertNotIn("post-process-command", payload["settings"])
        self.assertNotIn("personal-context", payload["settings"])
        self.assertNotIn("post-process-prompt", payload["settings"])
        self.assertNotIn("vocabulary", payload["settings"])
        self.assertIn("openai-compatible-api-key", payload["excluded_private_settings"])
        self.assertIn("personal-context", payload["excluded_private_settings"])
        self.assertIn("post-process-prompt", payload["excluded_private_settings"])
        self.assertIn("vocabulary", payload["excluded_private_settings"])
        self.assertIn("transcriber-command", payload["excluded_private_settings"])
        self.assertIn("post-process-command", payload["excluded_private_settings"])
        self.assertIn("cli-path", payload["excluded_private_settings"])
        self.assertEqual(payload["included_private_settings"], [])
        self.assertNotIn("sk-live-secret", rendered)
        self.assertNotIn("sk-secret-token", rendered)
        self.assertNotIn("ghp_secret", rendered)
        self.assertNotIn("PRIVATE PERSONAL CONTEXT", rendered)
        self.assertNotIn("PRIVATE POST PROCESS PROMPT", rendered)
        self.assertNotIn("PRIVATE VOCABULARY", rendered)

    def test_build_export_requires_explicit_private_setting_opt_in(self) -> None:
        settings = {
            "personal-context": "PRIVATE PERSONAL CONTEXT",
            "post-process-prompt": "PRIVATE POST PROCESS PROMPT",
            "vocabulary": "PRIVATE VOCABULARY",
        }
        with self.assertRaisesRegex(SettingsExportError, "private setting opt-in must be boolean"):
            build_export(settings, include_private_settings=1)  # type: ignore[arg-type]

        payload = build_export(settings, include_private_settings=True)
        self.assertEqual(payload["settings"]["personal-context"], settings["personal-context"])
        self.assertEqual(payload["settings"]["post-process-prompt"], settings["post-process-prompt"])
        self.assertEqual(payload["settings"]["vocabulary"], settings["vocabulary"])
        self.assertNotIn("personal-context", payload["excluded_private_settings"])
        self.assertIn("personal-context", payload["included_private_settings"])

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
        with self.assertRaisesRegex(SettingsExportError, "setting openai-compatible-url is missing hostname"):
            build_export({"openai-compatible-url": "https://:443/v1"})
        with self.assertRaisesRegex(SettingsExportError, "setting ollama-url is missing hostname"):
            build_export({"ollama-url": "https://@/v1"})

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

    def test_normalize_setting_canonicalizes_transcriber_aliases(self) -> None:
        for alias, canonical in {
            "openai": "whisper",
            "openai-whisper": "whisper",
            "external-api": "openai-compatible",
            "openai-compatible-api": "openai-compatible",
            "custom": "command",
            "template": "command",
        }.items():
            with self.subTest(alias=alias):
                self.assertEqual(normalize_setting("transcriber", alias), canonical)
        with self.assertRaisesRegex(SettingsExportError, "setting transcriber has unsupported value"):
            normalize_setting("transcriber", "shell")

    def test_write_and_read_export_round_trips_normalized_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            write_export(
                path,
                {
                    "auto-transcribe-timeout": False,
                    "auto-relisten": True,
                    "auto-paste-window-title": "Teams",
                    "show-transcript-text": False,
                    "status-icon-ready": "ready-01",
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
                include_private_settings=True,
            )
            payload = read_export(path)
        self.assertFalse(payload["settings"]["auto-transcribe-timeout"])
        self.assertTrue(payload["settings"]["auto-relisten"])
        self.assertEqual(payload["settings"]["auto-paste-window-title"], "Teams")
        self.assertFalse(payload["settings"]["show-transcript-text"])
        self.assertEqual(payload["settings"]["status-icon-ready"], "ready-01")
        self.assertEqual(payload["settings"]["max-transcript-files"], 500)
        self.assertEqual(payload["settings"]["artifact-encryption"], "keyring")
        self.assertFalse(payload["settings"]["notify-complete"])
        self.assertEqual(payload["settings"]["personal-context"], "Project words")
        self.assertIn("openai-compatible-api-key", payload["excluded_private_settings"])
        self.assertIn("transcriber-command", payload["excluded_private_settings"])
        self.assertIn("personal-context", payload["included_private_settings"])
        self.assertEqual(payload["alarms"]["last_checked_at"], "2026-06-01T09:10")
        self.assertEqual(payload["alarms"]["alarms"][0]["name"], "Standup")
        self.assertEqual(payload["alarms"]["alarms"][0]["days"], ["mon", "wed", "fri"])

    def test_settings_export_round_trips_multiline_personalization(self) -> None:
        settings = {
            "personal-context": "Project terminology\nKeep code unchanged",
            "vocabulary": "PipeWire\nCinnamon",
        }

        normalized = build_export(settings, include_private_settings=True)["settings"]
        self.assertEqual(normalized["personal-context"], settings["personal-context"])
        self.assertEqual(normalized["vocabulary"], settings["vocabulary"])

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            write_export(path, settings, include_private_settings=True)
            payload = read_export(path)
        self.assertEqual(payload["settings"]["personal-context"], settings["personal-context"])
        self.assertEqual(payload["settings"]["vocabulary"], settings["vocabulary"])

    def test_read_export_strips_private_settings_without_opt_in_metadata(self) -> None:
        payload = build_export({"language": "de"})
        payload["settings"]["personal-context"] = "injected context"
        payload["settings"]["post-process-prompt"] = "injected prompt"

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            imported = read_export(path)

        self.assertNotIn("personal-context", imported["settings"])
        self.assertNotIn("post-process-prompt", imported["settings"])

    def test_read_export_accepts_private_opt_in_without_excluded_metadata(self) -> None:
        payload = build_export(
            {"personal-context": "explicitly included"},
            include_private_settings=True,
        )
        payload.pop("excluded_private_settings")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            imported = read_export(path)

        self.assertEqual(imported["settings"]["personal-context"], "explicitly included")
        self.assertIn("personal-context", imported["included_private_settings"])
        self.assertNotIn("personal-context", imported["excluded_private_settings"])

    def test_read_export_rejects_conflicting_private_metadata(self) -> None:
        payload = build_export({"personal-context": "private"}, include_private_settings=True)
        payload["excluded_private_settings"].append("personal-context")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(SettingsExportError, "metadata conflicts"):
                read_export(path)

    def test_read_export_uses_schema_aligned_show_panel_label_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text(
                '{"app":"speed-of-cinnamon","version":2,"settings":{"language":"de"},'
                '"alarms":{"version":2,"alarms":[],"last_checked_at":""}}',
                encoding="utf-8",
            )
            payload = read_export(path)

        self.assertTrue(payload["settings"]["show-panel-label"])

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
        self.assertEqual(str(caught.exception.__cause__), "settings export operation failed")
        self.assertNotIn("disk full", str(caught.exception.__cause__))
        self.assertIn("settings export cleanup failed", "\n".join(caught.exception.__notes__))
        notes = "\n".join(caught.exception.__notes__)
        self.assertIn("settings export cleanup failed", notes)
        self.assertNotIn("cleanup denied", notes)

    def test_write_export_scrubs_temp_before_successful_unlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "settings-export.json"
            observed_contents: list[bytes] = []
            real_unlink = settings_export_module.os.unlink

            def observe_unlink(name: object, *args: object, **kwargs: object) -> None:
                if isinstance(name, str) and name.endswith(".cleanup"):
                    observed_contents.append((root / name).read_bytes())
                real_unlink(name, *args, **kwargs)

            real_rename = settings_export_module._rename_without_replacing

            def fail_activation_once(*args: object, **kwargs: object) -> None:
                if kwargs.get("field_name") == "settings export path":
                    raise OSError("activation failed")
                real_rename(*args, **kwargs)

            with (
                mock.patch.object(settings_export_module, "_rename_without_replacing", side_effect=fail_activation_once),
                mock.patch.object(settings_export_module.os, "unlink", side_effect=observe_unlink),
            ):
                with self.assertRaisesRegex(SettingsExportError, "failed to write settings export"):
                    write_export(path, {"personal-context": "PRIVATE EXPORT CONTENT"})

            self.assertEqual(len(observed_contents), 1)
            self.assertEqual(observed_contents[0], b"")

    def test_scrub_temp_reports_fsync_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            temp = root / ".settings-export.json.tmp"
            temp.write_bytes(b"PRIVATE EXPORT CONTENT")
            temp.chmod(0o600)
            parent_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                expected_stat = os.stat(temp, follow_symlinks=False)
                with mock.patch.object(settings_export_module, "_fsync_fd", side_effect=OSError("sync failed")):
                    with self.assertRaises((OSError, SettingsExportError)):
                        settings_export_module._scrub_temp_settings_export_file(
                            parent_fd,
                            temp.name,
                            expected_stat=expected_stat,
                        )
            finally:
                os.close(parent_fd)

    def test_write_export_does_not_restore_replaced_recovery_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "settings-export.json"
            path.write_text("old export\n", encoding="utf-8")
            foreign = root / "foreign.json"
            foreign.write_text("FOREIGN RECOVERY CONTENT", encoding="utf-8")
            foreign_anchor = root / "foreign-anchor.json"
            try:
                os.link(foreign, foreign_anchor)
            except OSError as exc:
                self.skipTest(f"hardlinks unavailable: {exc}")
            real_rename = settings_export_module._rename_without_replacing

            def replace_backup_with_foreign(
                source: str,
                target: str,
                *,
                directory_fd: int,
                field_name: str,
                **kwargs: object,
            ) -> None:
                real_rename(
                    source,
                    target,
                    directory_fd=directory_fd,
                    field_name=field_name,
                    **kwargs,
                )
                if source.endswith(".bak") and target.endswith(".cleanup"):
                    foreign.replace(root / target)

            with mock.patch.object(
                settings_export_module,
                "_rename_without_replacing",
                side_effect=replace_backup_with_foreign,
            ):
                with self._runtime_warning("settings export recovery backup cleanup failed"):
                    write_export(path, {"language": "de"})

            self.assertEqual(read_export(path)["settings"]["language"], "de")
            self.assertEqual(foreign_anchor.read_text(encoding="utf-8"), "FOREIGN RECOVERY CONTENT")
            self.assertTrue(foreign_anchor.exists())
            residuals = list(root.glob(".settings-export.json.*.bak.*.cleanup"))
            self.assertTrue(residuals)
            self.assertTrue(any(item.read_text(encoding="utf-8") == "FOREIGN RECOVERY CONTENT" for item in residuals))

    def test_red_write_export_does_not_unlink_temp_when_scrub_sync_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "settings-export.json"
            activation_failed = False
            scrub_sync_attempted = False
            observed_contents: list[bytes] = []
            real_fsync = os.fsync
            real_unlink = settings_export_module.os.unlink

            def fail_activation(*_args: object, **_kwargs: object) -> None:
                nonlocal activation_failed
                activation_failed = True
                raise OSError("activation failed")

            def fail_scrub_sync(fd: int) -> None:
                nonlocal scrub_sync_attempted
                if activation_failed:
                    scrub_sync_attempted = True
                    raise OSError("scrub sync failed")
                real_fsync(fd)

            def observe_unlink(name: object, *args: object, **kwargs: object) -> None:
                if isinstance(name, str) and name.endswith(".tmp"):
                    observed_contents.append((root / name).read_bytes())
                real_unlink(name, *args, **kwargs)

            with (
                mock.patch.object(settings_export_module, "_rename_without_replacing", side_effect=fail_activation),
                mock.patch.object(settings_export_module, "_fsync_fd", side_effect=fail_scrub_sync),
                mock.patch.object(settings_export_module.os, "unlink", side_effect=observe_unlink),
            ):
                with self.assertRaises(SettingsExportError) as caught:
                    write_export(path, {"personal-context": "PRIVATE EXPORT CONTENT"})

            self.assertTrue(scrub_sync_attempted)
            self.assertEqual(observed_contents, [])
            residuals = list(root.glob(".settings-export.json.*.tmp"))
            self.assertEqual(len(residuals), 1)
            self.assertNotIn(b"PRIVATE EXPORT CONTENT", residuals[0].read_bytes())
            notes = "\n".join(caught.exception.__notes__)
            self.assertIsInstance(caught.exception.__cause__, OSError)
            self.assertEqual(str(caught.exception.__cause__), "settings export operation failed")
            self.assertIn("settings export cleanup failed", notes)
            self.assertNotIn("scrub sync failed", notes)
            self.assertNotIn("PRIVATE EXPORT CONTENT", notes)

    def test_red_scrub_rejects_replaced_nonregular_inode_before_early_return(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            temp = root / ".settings-export.json.tmp"
            temp.write_bytes(b"PRIVATE EXPORT CONTENT")
            expected_stat = os.stat(temp, follow_symlinks=False)
            replaced_stat = mock.Mock(
                st_mode=stat.S_IFIFO | 0o600,
                st_dev=expected_stat.st_dev,
                st_ino=expected_stat.st_ino + 1,
                st_nlink=1,
                st_size=expected_stat.st_size,
                st_mtime_ns=expected_stat.st_mtime_ns,
                st_ctime_ns=expected_stat.st_ctime_ns,
            )
            with (
                mock.patch.object(settings_export_module.os, "open", return_value=123),
                mock.patch.object(settings_export_module.os, "fstat", return_value=replaced_stat),
                mock.patch.object(settings_export_module.os, "close"),
            ):
                with self.assertRaises(SettingsExportError):
                    settings_export_module._scrub_temp_settings_export_file(
                        456,
                        temp.name,
                        expected_stat=expected_stat,
                    )

    @unittest.skipUnless(getattr(os, "O_CLOEXEC", None) is not None, "O_CLOEXEC unavailable")
    def test_red_create_private_temp_file_passes_cloexec(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original_open = os.open
            original_close = os.close
            parent_fd = original_open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                with mock.patch.object(settings_export_module.os, "open", wraps=original_open) as mocked_open:
                    temp_fd, temp_name = settings_export_module._create_private_temp_file(
                        parent_fd,
                        "settings-export.json",
                    )
                original_close(temp_fd)
                original_unlink = os.unlink
                original_unlink(temp_name, dir_fd=parent_fd)
            finally:
                original_close(parent_fd)

            flags = mocked_open.call_args.args[1]
            self.assertTrue(flags & os.O_CLOEXEC)

    def test_red_recovery_backup_scrubs_before_unlink_and_does_not_restore_after_sync_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "settings-export.json"
            path.write_text("old export\n", encoding="utf-8")
            cleanup_started = False
            backup_cleanup_names: set[str] = set()
            observed_cleanup_unlinks: list[bytes] = []
            real_rename = settings_export_module._rename_without_replacing
            real_fsync = os.fsync
            real_unlink = settings_export_module.os.unlink

            def mark_backup_cleanup(source: object, target: object, *args: object, **kwargs: object) -> None:
                nonlocal cleanup_started
                real_rename(source, target, *args, **kwargs)
                if str(source).endswith(".bak") and str(target).endswith(".cleanup"):
                    cleanup_started = True
                    backup_cleanup_names.add(str(target))
                return None

            def fail_backup_scrub_sync(fd: int) -> None:
                if cleanup_started:
                    raise OSError("backup scrub sync failed")
                real_fsync(fd)

            def observe_cleanup_unlink(name: object, *args: object, **kwargs: object) -> None:
                if isinstance(name, str) and name in backup_cleanup_names:
                    observed_cleanup_unlinks.append((root / name).read_bytes())
                real_unlink(name, *args, **kwargs)

            with self._runtime_warning("settings export recovery backup cleanup failed"):
                with (
                    mock.patch.object(settings_export_module, "_rename_without_replacing", side_effect=mark_backup_cleanup),
                    mock.patch.object(settings_export_module, "_fsync_fd", side_effect=fail_backup_scrub_sync),
                    mock.patch.object(settings_export_module.os, "unlink", side_effect=observe_cleanup_unlink),
                ):
                    write_export(path, {"language": "de"})

            self.assertEqual(read_export(path)["settings"]["language"], "de")
            self.assertEqual(observed_cleanup_unlinks, [])
            self.assertFalse(list(root.glob(".settings-export.json.*.bak")))
            residuals = list(root.glob(".settings-export.json.*.cleanup"))
            self.assertTrue(residuals)
            self.assertEqual(len(residuals), 1)
            self.assertNotIn(b"old export\n", residuals[0].read_bytes())

    def test_write_export_returns_fixed_post_commit_warning_payload_without_persisting_it(self) -> None:
        fixed_warning = (
            "settings export committed but settings export recovery backup cleanup failed; "
            "private backup data may remain"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text("old export\n", encoding="utf-8")
            real_unlink = os.unlink

            def fail_backup_cleanup(name: object, *args: object, **kwargs: object) -> None:
                if isinstance(name, str) and ".bak." in name and name.endswith(".cleanup"):
                    raise OSError("/secret/recovery-cleanup")
                real_unlink(name, *args, **kwargs)

            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                with mock.patch.object(settings_export_module.os, "unlink", side_effect=fail_backup_cleanup):
                    payload = write_export(path, {"language": "de"})

            self.assertEqual(payload["post_commit_warnings"], [fixed_warning])
            on_disk = read_export(path)
            self.assertNotIn("post_commit_warnings", on_disk)
            self.assertNotIn("/secret/recovery-cleanup", repr(payload))
            self.assertNotIn("/secret/recovery-cleanup", repr(on_disk))

    def test_write_export_commits_when_runtime_warning_is_error(self) -> None:
        fixed_warning = (
            "settings export committed but settings export recovery backup cleanup failed; "
            "private backup data may remain"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text("old export\n", encoding="utf-8")
            real_unlink = os.unlink

            def fail_backup_cleanup(name: object, *args: object, **kwargs: object) -> None:
                if isinstance(name, str) and ".bak." in name and name.endswith(".cleanup"):
                    raise OSError("/secret/recovery-cleanup")
                real_unlink(name, *args, **kwargs)

            with warnings.catch_warnings():
                warnings.simplefilter("error", RuntimeWarning)
                with mock.patch.object(settings_export_module.os, "unlink", side_effect=fail_backup_cleanup):
                    payload = write_export(path, {"language": "de"})

            self.assertEqual(payload["post_commit_warnings"], [fixed_warning])
            on_disk = read_export(path)
            self.assertEqual(on_disk["settings"]["language"], "de")
            self.assertNotIn("post_commit_warnings", on_disk)
            self.assertNotIn("/secret/recovery-cleanup", repr(payload))
            self.assertNotIn("/secret/recovery-cleanup", repr(on_disk))

    def test_cleanup_failure_note_does_not_include_raw_cleanup_exception(self) -> None:
        primary = SettingsExportError("primary export failure")
        cleanup_error = OSError("/secret/cleanup failure")

        settings_export_module._note_cleanup_failure(primary, cleanup_error)

        notes = "\n".join(getattr(primary, "__notes__", ()))
        self.assertIn("settings export cleanup failed", notes)
        self.assertNotIn(str(cleanup_error), notes)
        self.assertNotIn("/secret/cleanup failure", notes)

    def test_missing_nonblocking_flag_rejects_recovery_leaf_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text("old export\n", encoding="utf-8")
            real_open = settings_export_module.os.open
            backup_claim_flags: list[int] = []

            def record_backup_claim(name: object, flags: int, *args: object, **kwargs: object) -> int:
                if isinstance(name, str) and name.endswith(".bak"):
                    backup_claim_flags.append(flags)
                return real_open(name, flags, *args, **kwargs)

            with (
                mock.patch.object(settings_export_module.os, "O_NONBLOCK", 0, create=True),
                mock.patch.object(settings_export_module.os, "open", side_effect=record_backup_claim),
            ):
                with self.assertRaises(SettingsExportError):
                    write_export(path, {"language": "de"})

            self.assertEqual(backup_claim_flags, [])

    def test_red_rename_claim_rejects_unbound_typeerror_without_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text("old export\n", encoding="utf-8")
            real_rename = settings_export_module._rename_without_replacing
            unbound_claims: list[tuple[object, object]] = []

            def guarded_rename(
                source: object,
                target: object,
                *,
                directory_fd: int,
                field_name: str,
                **kwargs: object,
            ) -> None:
                is_claim = str(target).endswith(".cleanup") or str(source).endswith(".cleanup")
                if is_claim and not {
                    "expected_source_stat",
                    "expected_source_fd",
                }.issubset(kwargs):
                    unbound_claims.append((source, target))
                    raise TypeError("unbound rename claim")
                real_rename(
                    source,
                    target,
                    directory_fd=directory_fd,
                    field_name=field_name,
                    **kwargs,
                )

            with mock.patch.object(settings_export_module, "_rename_without_replacing", side_effect=guarded_rename):
                try:
                    write_export(path, {"language": "de"})
                except TypeError as exc:
                    self.fail(f"unbound rename claim escaped: {exc}")

            self.assertEqual(unbound_claims, [])

    @unittest.skipUnless(getattr(os, "O_PATH", None), "O_PATH is required for symlink cleanup claims")
    def test_red_symlink_candidate_uses_bound_cleanup_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "settings-export.json"
            path.write_text("old export\n", encoding="utf-8")
            replacement = root / "replacement.json"
            replacement.write_text("foreign export\n", encoding="utf-8")
            real_rename = settings_export_module._rename_without_replacing
            candidate_claims: list[dict[str, object]] = []
            unbound_claims: list[tuple[object, object]] = []

            def link_as_symlink(_source: object, target: object, **_kwargs: object) -> None:
                (root / str(target)).symlink_to(replacement)

            def guarded_rename(
                source: object,
                target: object,
                *,
                directory_fd: int,
                field_name: str,
                **kwargs: object,
            ) -> None:
                if field_name == "settings export recovery backup candidate cleanup":
                    candidate_claims.append(dict(kwargs))
                    if not {
                        "expected_source_stat",
                        "expected_source_fd",
                    }.issubset(kwargs):
                        unbound_claims.append((source, target))
                        raise TypeError("unbound symlink candidate claim")
                real_rename(
                    source,
                    target,
                    directory_fd=directory_fd,
                    field_name=field_name,
                    **kwargs,
                )

            with (
                mock.patch.object(settings_export_module.os, "link", side_effect=link_as_symlink),
                mock.patch.object(settings_export_module, "_rename_without_replacing", side_effect=guarded_rename),
            ):
                with self.assertRaises(SettingsExportError):
                    write_export(path, {"language": "de"})

            self.assertEqual(len(candidate_claims), 1)
            self.assertEqual(unbound_claims, [])

    def test_write_export_does_not_copy_source_exception_notes_to_public_error(self) -> None:
        secret = "/secret/settings-export-source-note"
        source_error = OSError("activation failed")
        source_error.add_note(secret)

        def fail_activation(*args: object, **kwargs: object) -> None:
            raise source_error

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            with mock.patch.object(
                settings_export_module,
                "_rename_without_replacing",
                side_effect=fail_activation,
            ):
                with self.assertRaises(settings_export_module.SettingsExportError) as raised:
                    write_export(target, {"language": "en"})

        public_error = raised.exception
        self.assertNotIn(secret, repr(public_error))
        self.assertNotIn(secret, "\n".join(getattr(public_error, "__notes__", ())))
        cause = public_error.__cause__
        if cause is not None:
            self.assertNotIn(secret, repr(cause))
            self.assertNotIn(secret, "\n".join(getattr(cause, "__notes__", ())))

    def test_positive_readonly_target_remains_exportable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text("old export\n", encoding="utf-8")
            path.chmod(0o444)

            write_export(path, {"language": "de"})

            self.assertEqual(read_export(path)["settings"]["language"], "de")
            residuals = list(path.parent.glob(".settings-export.json.*.bak")) + list(
                path.parent.glob(".settings-export.json.*.cleanup")
            )
            self.assertFalse(residuals)

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
        self.assertGreaterEqual(mocked_rename.call_count, 1)

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
                **kwargs: object,
            ) -> None:
                if target == path.name:
                    replacement.replace(path)
                real_rename(source, target, directory_fd=directory_fd, field_name=field_name, **kwargs)

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
                side_effect=["temp", "fixed", "free", "target-cleanup", "cleanup"],
            ):
                write_export(path, {"language": "de"})

            self.assertEqual(racing_candidate.read_text(encoding="utf-8"), "racing backup\n")
            self.assertFalse((Path(tmp) / ".settings-export.json.free.bak").exists())
            self.assertEqual(read_export(path)["settings"]["language"], "de")

    def test_write_export_restores_target_when_it_disappears_during_backup_inspection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text("old export\n", encoding="utf-8")
            real_stat = settings_export_module.os.stat
            target_removed = False

            def stat_then_remove_target(
                name: object,
                *args: object,
                **kwargs: object,
            ) -> os.stat_result:
                nonlocal target_removed
                result = real_stat(name, *args, **kwargs)
                if isinstance(name, str) and name.endswith(".bak") and not target_removed:
                    path.unlink()
                    target_removed = True
                return result

            with mock.patch.object(settings_export_module.os, "stat", side_effect=stat_then_remove_target):
                with self.assertRaisesRegex(SettingsExportError, "failed to write settings export"):
                    write_export(path, {"language": "de"})

            self.assertTrue(target_removed)
            self.assertEqual(path.read_text(encoding="utf-8"), "old export\n")
            self.assertFalse(list(Path(tmp).glob(".settings-export.json.*.bak")))
            self.assertFalse(list(Path(tmp).glob(".settings-export.json.*.tmp")))

    @unittest.skipUnless(getattr(os, "O_PATH", None), "O_PATH is required for symlink cleanup claims")
    def test_write_export_preserves_untrusted_recovery_symlink_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text("old export\n", encoding="utf-8")
            replacement = Path(tmp) / "replacement.json"
            replacement.write_text("foreign export\n", encoding="utf-8")

            def link_as_symlink(_source: object, target: object, **_kwargs: object) -> None:
                (Path(tmp) / str(target)).symlink_to(replacement)

            with mock.patch.object(settings_export_module.os, "link", side_effect=link_as_symlink):
                with self.assertRaisesRegex(SettingsExportError, "failed to write settings export"):
                    write_export(path, {"language": "de"})

            self.assertEqual(path.read_text(encoding="utf-8"), "old export\n")
            candidates = list(Path(tmp).glob(".settings-export.json.*.bak"))
            self.assertEqual(len(candidates), 1)
            self.assertTrue(candidates[0].is_symlink())
            self.assertEqual(candidates[0].readlink(), replacement)
            self.assertFalse(list(Path(tmp).glob(".settings-export.json.*.tmp")))
            self.assertTrue(replacement.exists())
            self.assertEqual(replacement.read_text(encoding="utf-8"), "foreign export\n")

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

    def test_write_export_does_not_remove_in_place_changed_temp_file(self) -> None:
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
                    assert temp_fd is not None
                    os.write(temp_fd, b"in-place replacement")
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
            self.assertEqual(temp_path.read_bytes(), b"in-place replacement")

    def test_write_export_preserves_fdopen_error_when_temp_fd_close_is_interrupted(self) -> None:
        def close(fd: int) -> None:
            if fd == 123:
                raise KeyboardInterrupt

        with (
            mock.patch.object(settings_export_module, "_assert_clean_path"),
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
            mock.patch.object(settings_export_module, "_assert_clean_path"),
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
        notes = "\n".join(caught.exception.__notes__)
        self.assertIn("settings export cleanup failed", notes)
        self.assertNotIn("temp close failed", notes)

    def test_write_export_preserves_write_error_when_temporary_handle_close_fails(self) -> None:
        class _Handle:
            def fileno(self) -> int:
                return 123

            def write(self, _payload: str) -> int:
                raise OSError("write failed")

            def flush(self) -> None:
                return None

            def close(self) -> None:
                raise OSError("close failed: /secret/close")

        with (
            mock.patch.object(settings_export_module, "_assert_clean_path"),
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

        self.assertIsInstance(caught.exception.__cause__, OSError)
        self.assertEqual(str(caught.exception.__cause__), "settings export operation failed")
        notes = "\n".join(caught.exception.__notes__)
        self.assertIn("settings export cleanup failed", notes)
        self.assertNotIn("close failed", notes)
        self.assertNotIn("/secret/close", notes)

    def test_write_export_wraps_temporary_write_memory_error(self) -> None:
        class _Handle:
            def __init__(self, fd: int) -> None:
                self.fd = fd

            def fileno(self) -> int:
                return self.fd

            def write(self, _payload: str) -> int:
                raise MemoryError("write exhausted")

            def flush(self) -> None:
                return None

            def close(self) -> None:
                os.close(self.fd)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"

            def fdopen(fd: int, _mode: str, **_kwargs: object) -> _Handle:
                return _Handle(fd)

            with mock.patch.object(settings_export_module.os, "fdopen", side_effect=fdopen):
                with self.assertRaisesRegex(SettingsExportError, "failed to write settings export"):
                    write_export(path, {"language": "en"})

            self.assertFalse(path.exists())
            self.assertEqual(list(Path(tmp).glob(".settings-export.json.*.tmp")), [])

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

    def test_read_export_preserves_read_interrupt_with_redacted_cleanup_note(self) -> None:
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
        notes = "\n".join(caught.exception.__notes__)
        self.assertIn("settings export cleanup failed", notes)
        self.assertNotIn("close failed", notes)

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
            mock.patch.object(settings_export_module, "_fsync_fd"),
        ):
            settings_export_module._scrub_temp_settings_export_file(456, ".settings.tmp")

        flags = mocked_open.call_args.args[1]
        self.assertTrue(flags & getattr(os, "O_NONBLOCK", 0))

    def test_scrub_temp_file_retries_interrupted_writes(self) -> None:
        with (
            mock.patch.object(settings_export_module.os, "open", return_value=123),
            mock.patch.object(
                settings_export_module.os,
                "fstat",
                return_value=mock.Mock(st_mode=stat.S_IFREG, st_size=3, st_dev=1, st_ino=2, st_nlink=1),
            ),
            mock.patch.object(settings_export_module.os, "lseek"),
            mock.patch.object(
                settings_export_module.os,
                "write",
                side_effect=[InterruptedError(), 3],
            ) as mocked_write,
            mock.patch.object(settings_export_module.os, "ftruncate"),
            mock.patch.object(settings_export_module.os, "close"),
            mock.patch.object(settings_export_module, "_fsync_fd"),
        ):
            settings_export_module._scrub_temp_settings_export_file(456, ".settings.tmp")

        self.assertEqual(mocked_write.call_count, 2)

    def test_scrub_temp_file_retries_interrupted_sync_and_truncate(self) -> None:
        with (
            mock.patch.object(settings_export_module.os, "open", return_value=123),
            mock.patch.object(
                settings_export_module.os,
                "fstat",
                return_value=mock.Mock(st_mode=stat.S_IFREG, st_size=0),
            ),
            mock.patch.object(
                settings_export_module.os,
                "ftruncate",
                side_effect=[InterruptedError(), None],
            ) as mocked_ftruncate,
            mock.patch.object(
                settings_export_module.os,
                "fsync",
                side_effect=[InterruptedError(), None],
            ) as mocked_fsync,
            mock.patch.object(settings_export_module.os, "close"),
        ):
            settings_export_module._scrub_temp_settings_export_file(456, ".settings.tmp")

        self.assertEqual(mocked_ftruncate.call_count, 2)
        self.assertEqual(mocked_fsync.call_count, 2)

    def test_write_export_warns_on_parent_close_failure_after_successful_write(self) -> None:
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
                with self._runtime_warning("settings export directory close failed"):
                    write_export(path, {"language": "en"})

            self.assertTrue(path.exists())

    def test_write_export_warns_when_recovery_backup_scrub_write_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text("old export\n", encoding="utf-8")

            with (
                mock.patch.object(
                    settings_export_module,
                    "_scrub_settings_export_fd",
                    side_effect=OSError("backup scrub write failed: /secret/old-export"),
                ),
                self._runtime_warning("settings export recovery backup cleanup failed") as captured_warnings,
            ):
                write_export(path, {"language": "de"})

            self.assertEqual(read_export(path)["settings"]["language"], "de")
            residuals = list(Path(tmp).glob(".settings-export.json.*.bak")) + list(
                Path(tmp).glob(".settings-export.json.*.bak.*.cleanup")
            )
            self.assertTrue(residuals)
            warning_text = "\n".join(str(item.message) for item in captured_warnings)
            self.assertNotIn(str(path), warning_text)
            self.assertNotIn("/secret/old-export", warning_text)

    def test_write_export_propagates_recovery_backup_scrub_interrupt_after_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text("old export\n", encoding="utf-8")

            with mock.patch.object(
                settings_export_module,
                "_scrub_settings_export_fd",
                side_effect=KeyboardInterrupt("backup scrub interrupted: /secret/old-export"),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    write_export(path, {"language": "de"})

            self.assertEqual(read_export(path)["settings"]["language"], "de")

    def test_write_export_preserves_claim_rename_error_when_claim_close_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text("old export\n", encoding="utf-8")
            real_open = settings_export_module.os.open
            real_close = settings_export_module.os.close
            claim_fds: list[int] = []

            def record_claim_open(name: object, flags: int, *args: object, **kwargs: object) -> int:
                fd = real_open(name, flags, *args, **kwargs)
                if isinstance(name, str) and name == path.name:
                    claim_fds.append(fd)
                return fd

            def close_with_failure(fd: int) -> None:
                if fd in claim_fds:
                    raise OSError("claim close failed: /secret/claim")
                real_close(fd)

            with (
                mock.patch.object(
                    settings_export_module,
                    "_rename_without_replacing",
                    side_effect=OSError("claim rename failed: /secret/claim"),
                ),
                mock.patch.object(settings_export_module.os, "open", side_effect=record_claim_open),
                mock.patch.object(settings_export_module.os, "close", side_effect=close_with_failure),
            ):
                with self.assertRaises(SettingsExportError) as caught:
                    write_export(path, {"language": "de"})

            self.assertIs(type(caught.exception), SettingsExportError)
            cause = caught.exception.__cause__
            self.assertIsInstance(cause, OSError)
            self.assertEqual(str(cause), "settings export operation failed")
            self.assertNotIn("/secret/claim", str(cause))
            cause_notes = "\n".join(getattr(cause, "__notes__", ()))
            self.assertIn("settings export cleanup failed", cause_notes)
            self.assertNotIn("claim close failed", cause_notes)
            self.assertNotIn("/secret/claim", cause_notes)

    @unittest.skipUnless(getattr(os, "O_NONBLOCK", None) is not None, "O_NONBLOCK unavailable")
    def test_write_export_uses_nonblocking_recovery_backup_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text("old export\n", encoding="utf-8")
            real_open = settings_export_module.os.open
            claim_open_calls: list[tuple[str, int]] = []

            def record_backup_claim(name: object, flags: int, *args: object, **kwargs: object) -> int:
                if isinstance(name, str) and name.endswith(".bak"):
                    claim_open_calls.append((name, flags))
                return real_open(name, flags, *args, **kwargs)

            with mock.patch.object(settings_export_module.os, "open", side_effect=record_backup_claim):
                write_export(path, {"language": "de"})

            self.assertTrue(claim_open_calls)
            self.assertTrue(
                all(flags & getattr(os, "O_NONBLOCK", 0) for _name, flags in claim_open_calls),
                claim_open_calls,
            )

    @unittest.skipUnless(
        getattr(os, "O_NOFOLLOW", None) is not None and getattr(os, "O_NONBLOCK", None) is not None,
        "portable leaf-claim flags unavailable",
    )
    def test_write_export_fallback_leaf_claims_use_nonblocking_open_without_o_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text("old export\n", encoding="utf-8")
            real_open = settings_export_module.os.open
            real_fsync = settings_export_module._fsync_fd
            leaf_claim_calls: list[tuple[str, int]] = []
            parent_syncs = 0

            def record_leaf_claim(name: object, flags: int, *args: object, **kwargs: object) -> int:
                if isinstance(name, str):
                    basename = Path(name).name
                    if basename == path.name or basename.endswith(".bak") or basename.endswith(".cleanup"):
                        leaf_claim_calls.append((name, flags))
                return real_open(name, flags, *args, **kwargs)

            def fail_activation_sync(fd: int) -> None:
                nonlocal parent_syncs
                try:
                    is_directory = stat.S_ISDIR(os.fstat(fd).st_mode)
                except OSError:
                    is_directory = False
                if is_directory:
                    parent_syncs += 1
                    if parent_syncs == 2:
                        raise OSError("activation sync failed")
                real_fsync(fd)

            with (
                mock.patch.object(settings_export_module.os, "O_PATH", 0, create=True),
                mock.patch.object(settings_export_module.os, "open", side_effect=record_leaf_claim),
                mock.patch.object(settings_export_module, "_fsync_fd", side_effect=fail_activation_sync),
            ):
                with self.assertRaisesRegex(SettingsExportError, "failed to write settings export"):
                    write_export(path, {"language": "de"})

            self.assertGreaterEqual(parent_syncs, 2)
            self.assertTrue(leaf_claim_calls)
            self.assertTrue(
                all(flags & os.O_NONBLOCK for _name, flags in leaf_claim_calls),
                leaf_claim_calls,
            )

    def test_write_export_restores_backup_when_recovery_backup_fchmod_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text("old export\n", encoding="utf-8")

            with (
                mock.patch.object(settings_export_module.os, "fchmod", side_effect=OSError("backup chmod failed")),
                self._runtime_warning("settings export recovery backup cleanup failed"),
            ):
                write_export(path, {"language": "de"})

            self.assertEqual(read_export(path)["settings"]["language"], "de")
            backups = list(Path(tmp).glob(".settings-export.json.*.bak"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), "old export\n")
            self.assertFalse(list(Path(tmp).glob(".settings-export.json.*.bak.*.cleanup")))

    def test_write_export_restores_backup_when_recovery_backup_scrub_open_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text("old export\n", encoding="utf-8")
            real_open = settings_export_module.os.open

            def fail_backup_scrub_open(name: object, flags: int, *args: object, **kwargs: object) -> int:
                if isinstance(name, str) and name.endswith(".cleanup"):
                    raise OSError("backup scrub open failed")
                return real_open(name, flags, *args, **kwargs)

            with (
                mock.patch.object(settings_export_module.os, "open", side_effect=fail_backup_scrub_open),
                self._runtime_warning("settings export recovery backup cleanup failed"),
            ):
                write_export(path, {"language": "de"})

            self.assertEqual(read_export(path)["settings"]["language"], "de")
            backups = list(Path(tmp).glob(".settings-export.json.*.bak"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), "old export\n")
            self.assertFalse(list(Path(tmp).glob(".settings-export.json.*.bak.*.cleanup")))

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

    def test_write_export_rejects_symlinked_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp) / "outside"
            outside.mkdir()
            linked_parent = Path(tmp) / "linked-parent"
            linked_parent.symlink_to(outside, target_is_directory=True)
            path = linked_parent / "settings-export.json"

            with self.assertRaisesRegex(SettingsExportError, "must not pass through a symlink"):
                write_export(path, {"language": "en"})

            self.assertFalse((outside / "settings-export.json").exists())

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
                if isinstance(name, str) and ".bak." in name and name.endswith(".cleanup"):
                    raise OSError("backup cleanup failed")
                real_unlink(name, *args, **kwargs)

            with self._runtime_warning("settings export recovery backup cleanup failed"):
                with mock.patch.object(settings_export_module.os, "unlink", side_effect=fail_backup_cleanup):
                    write_export(path, {"language": "de"})

            self.assertEqual(read_export(path)["settings"]["language"], "de")
            self.assertFalse(list(Path(tmp).glob(".settings-export.json.*.bak")))
            cleanup_files = list(Path(tmp).glob(".settings-export.json.*.bak.*.cleanup"))
            self.assertEqual(len(cleanup_files), 1)
            self.assertTrue(cleanup_files)
            self.assertTrue(all(b"old export\n" not in item.read_bytes() for item in cleanup_files))

    def test_write_export_preserves_success_when_recovery_backup_cleanup_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text("old export\n", encoding="utf-8")
            real_unlink = os.unlink

            def interrupt_backup_cleanup(name: object, *args: object, **kwargs: object) -> None:
                if isinstance(name, str) and ".bak." in name and name.endswith(".cleanup"):
                    raise KeyboardInterrupt("backup cleanup interrupted")
                real_unlink(name, *args, **kwargs)

            with mock.patch.object(settings_export_module.warnings, "warn") as warned:
                with mock.patch.object(settings_export_module.os, "unlink", side_effect=interrupt_backup_cleanup):
                    with self.assertRaises(KeyboardInterrupt):
                        write_export(path, {"language": "de"})

            self.assertEqual(read_export(path)["settings"]["language"], "de")
            warned.assert_not_called()
            self.assertFalse(list(Path(tmp).glob(".settings-export.json.*.bak")))
            cleanup_files = list(Path(tmp).glob(".settings-export.json.*.bak.*.cleanup"))
            self.assertEqual(len(cleanup_files), 1)
            self.assertTrue(cleanup_files)
            self.assertTrue(all(b"old export\n" not in item.read_bytes() for item in cleanup_files))

    def test_write_export_preserves_changed_recovery_backup_during_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            replacement = Path(tmp) / "replacement.json"
            path.write_text("old export\n", encoding="utf-8")
            replacement.write_text("foreign export\n", encoding="utf-8")
            replacement.chmod(0o600)
            real_stat = settings_export_module.os.stat
            backup_stat_calls = 0

            def stat_then_swap_after_cleanup_check(
                name: object,
                *args: object,
                **kwargs: object,
            ) -> os.stat_result:
                nonlocal backup_stat_calls
                result = real_stat(name, *args, **kwargs)
                if isinstance(name, str) and name.endswith(".bak"):
                    backup_stat_calls += 1
                    if backup_stat_calls == 2:
                        backup_path = path.parent / name
                        backup_path.unlink()
                        replacement.replace(backup_path)
                return result

            with mock.patch.object(
                settings_export_module.os,
                "stat",
                side_effect=stat_then_swap_after_cleanup_check,
            ):
                with self._runtime_warning("settings export recovery backup cleanup failed"):
                    write_export(path, {"language": "de"})

            self.assertEqual(read_export(path)["settings"]["language"], "de")
            backups = list(Path(tmp).glob(".settings-export.json.*.bak"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), "foreign export\n")

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

    def test_write_export_keeps_new_target_when_recovery_backup_is_replaced_during_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text("old export\n", encoding="utf-8")
            path.chmod(0o600)
            real_fsync = os.fsync
            directory_syncs = 0
            replacement = Path(tmp) / "replacement.json"
            replacement.write_text("replacement export\n", encoding="utf-8")

            def fail_after_replacing_backup(fd: int) -> None:
                nonlocal directory_syncs
                mode = os.fstat(fd).st_mode
                if stat.S_ISDIR(mode):
                    directory_syncs += 1
                    if directory_syncs == 3:
                        backups = list(Path(tmp).glob(".settings-export.json.*.bak"))
                        self.assertEqual(len(backups), 1)
                        replacement.replace(backups[0])
                        raise OSError("activation directory sync failed")
                real_fsync(fd)

            with mock.patch.object(settings_export_module.os, "fsync", side_effect=fail_after_replacing_backup):
                with self.assertRaisesRegex(SettingsExportError, "failed to write settings export"):
                    write_export(path, {"language": "de"})

            self.assertEqual(read_export(path)["settings"]["language"], "de")
            backups = list(Path(tmp).glob(".settings-export.json.*.bak"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), "replacement export\n")

    def test_write_export_restores_target_when_backup_unlink_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text("old export\n", encoding="utf-8")
            path.chmod(0o600)
            real_unlink = os.unlink
            interrupted = False

            def unlink_then_interrupt(name: object, *args: object, **kwargs: object) -> None:
                nonlocal interrupted
                if isinstance(name, str) and name.endswith(".cleanup") and not interrupted:
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

    def test_write_export_preserves_target_replacement_after_target_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            replacement = Path(tmp) / "replacement.json"
            path.write_text("old export\n", encoding="utf-8")
            replacement.write_text("replacement export\n", encoding="utf-8")
            real_exchange = settings_export_module._rename_exchange
            swapped = False

            def exchange_then_swap(source: object, target: object, *args: object, **kwargs: object) -> None:
                nonlocal swapped
                if not swapped and target == path.name:
                    replacement.replace(path)
                    swapped = True
                real_exchange(source, target, *args, **kwargs)

            with mock.patch.object(settings_export_module, "_rename_exchange", side_effect=exchange_then_swap):
                with self.assertRaisesRegex(SettingsExportError, "failed to write settings export"):
                    write_export(path, {"language": "de"})

            self.assertEqual(path.read_text(encoding="utf-8"), "replacement export\n")
            self.assertFalse(list(Path(tmp).glob(".settings-export.json.*.bak")))

    def test_write_export_rejects_target_replacement_during_activation_inspection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            replacement = Path(tmp) / "replacement.json"
            path.write_text("old export\n", encoding="utf-8")
            replacement.write_text("replacement export\n", encoding="utf-8")
            real_stat = settings_export_module.os.stat
            target_stats = 0

            def stat_then_swap(
                name: object,
                *args: object,
                **kwargs: object,
            ) -> os.stat_result:
                nonlocal target_stats
                result = real_stat(name, *args, **kwargs)
                if name == path.name and kwargs.get("dir_fd") is not None:
                    target_stats += 1
                    if target_stats == 5:
                        replacement.replace(path)
                        return real_stat(name, *args, **kwargs)
                return result

            with mock.patch.object(settings_export_module.os, "stat", side_effect=stat_then_swap):
                with self.assertRaisesRegex(SettingsExportError, "failed to write settings export"):
                    write_export(path, {"language": "de"})

            self.assertEqual(path.read_text(encoding="utf-8"), "replacement export\n")
            self.assertFalse(list(Path(tmp).glob(".settings-export.json.*.tmp")))

    def test_write_export_preserves_in_place_target_change_after_activation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text("old export\n", encoding="utf-8")
            real_stat = settings_export_module.os.stat
            target_stats = 0

            def mutate_before_post_activation_stat(
                name: object,
                *args: object,
                **kwargs: object,
            ) -> os.stat_result:
                nonlocal target_stats
                if name == path.name and kwargs.get("dir_fd") is not None:
                    target_stats += 1
                    if target_stats == 5:
                        payload = path.read_bytes()
                        path.write_bytes(b"X" * len(payload))
                        raise OSError("post-activation inspection failed")
                return real_stat(name, *args, **kwargs)

            with mock.patch.object(settings_export_module.os, "stat", side_effect=mutate_before_post_activation_stat):
                with self.assertRaisesRegex(SettingsExportError, "failed to write settings export"):
                    write_export(path, {"language": "de"})

            payload = path.read_bytes()
            self.assertEqual(payload, b"X" * len(payload))
            self.assertTrue(list(Path(tmp).glob(".settings-export.json.*.bak")))
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
            residual_tmp = list(Path(tmp).glob(".settings-export.json.*.tmp"))
            self.assertEqual(len(residual_tmp), 1)
            self.assertTrue(all(b"language" not in item.read_bytes() for item in residual_tmp))
            self.assertTrue(fsynced_modes)

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

    def test_build_export_rejects_alarm_store_timestamp_above_store_limit(self) -> None:
        with self.assertRaisesRegex(SettingsExportError, "settings export alarm last_checked_at is too long"):
            build_export({"language": "de"}, {"alarms": [], "last_checked_at": "T" * 41})

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

class SettingsExportSecurityRegressionTests(unittest.TestCase):
    def test_write_export_rollback_preserves_target_after_partial_write_mutation(self) -> None:
        from speed_of_cinnamon import settings_export as settings_export_module

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text("old export\n", encoding="utf-8")
            foreign_content = b"FOREIGN PARTIAL-WRITE TARGET\n"
            mutation_seen = False

            def mutate_then_fail(*args: object, **kwargs: object) -> None:
                nonlocal mutation_seen
                if not mutation_seen:
                    path.write_bytes(foreign_content)
                    mutation_seen = True
                raise OSError("partial write failed")

            with mock.patch.object(
                settings_export_module,
                "_rename_without_replacing",
                side_effect=mutate_then_fail,
            ):
                with self.assertRaises(settings_export_module.SettingsExportError):
                    settings_export_module.write_export(path, {"language": "en"})

            self.assertTrue(mutation_seen)
            self.assertEqual(path.read_bytes(), foreign_content)

    def test_write_export_redacts_public_cause_args_and_filename(self) -> None:
        import traceback

        from speed_of_cinnamon import settings_export as settings_export_module

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings-export.json"
            path.write_text("old export\n", encoding="utf-8")
            secret = "/secret/settings-export-filename"
            source_error = OSError(secret)
            source_error.filename = secret
            source_error.add_note(secret + "-note")

            with mock.patch.object(
                settings_export_module,
                "_rename_without_replacing",
                side_effect=source_error,
            ):
                with self.assertRaises(settings_export_module.SettingsExportError) as caught:
                    settings_export_module.write_export(path, {"language": "en"})

            public_error = caught.exception
            public_cause = public_error.__cause__
            self.assertIsNotNone(public_cause)
            channels = (
                str(public_error),
                repr(public_error),
                repr(public_error.args),
                repr(getattr(public_error, "__notes__", ())),
                "".join(traceback.format_exception(public_error)),
                str(public_cause),
                repr(public_cause),
                repr(public_cause.args),
                repr(getattr(public_cause, "filename", None)),
                repr(getattr(public_cause, "__notes__", ())),
            )
            for channel in channels:
                self.assertNotIn(secret, channel)


if __name__ == "__main__":
    unittest.main()
