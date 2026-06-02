from __future__ import annotations

import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from speed_of_cinnamon import doctor


def which_from(names: set[str]) -> mock.Mock:
    return mock.Mock(side_effect=lambda name: f"/usr/bin/{name}" if name in names else None)


class DoctorTest(unittest.TestCase):
    def test_default_pipeline_reports_missing_asr(self) -> None:
        tools = {"python3", "pw-record", "pactl", "xdotool"}
        env = {"XDG_CURRENT_DESKTOP": "X-Cinnamon", "XDG_SESSION_TYPE": "x11", "DESKTOP_SESSION": "cinnamon"}
        with (
            mock.patch("speed_of_cinnamon.doctor.default_ctranslate2_model_path", return_value=""),
            mock.patch("speed_of_cinnamon.doctor.default_whisper_cpp_model_path", return_value=""),
            mock.patch("speed_of_cinnamon.doctor.shutil.which", which_from(tools)),
            mock.patch.dict(os.environ, env),
        ):
            payload = doctor.report({"recorder": "auto", "transcriber": "auto", "insert-method": "clipboard-paste"})
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["desktop"]["cinnamon"])
        self.assertTrue(payload["configured"]["recorder"]["ok"])
        self.assertFalse(payload["configured"]["transcriber"]["ok"])
        self.assertIn("install whisper", payload["configured"]["transcriber"]["detail"])

    def test_custom_command_pipeline_allows_copy_only_without_xdotool(self) -> None:
        tools = {"python3", "pw-record", "pactl"}
        env = {"XDG_CURRENT_DESKTOP": "X-Cinnamon", "XDG_SESSION_TYPE": "x11", "DESKTOP_SESSION": "cinnamon"}
        settings = {
            "recorder": "auto",
            "transcriber": "command",
            "transcriber-command": "printf ok",
            "insert-method": "clipboard-paste",
        }
        with mock.patch("speed_of_cinnamon.doctor.shutil.which", which_from(tools)), mock.patch.dict(os.environ, env):
            payload = doctor.report(settings, applet=True)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["configured"]["output"]["ok"])
        self.assertFalse(payload["configured"]["output"]["paste_ok"])
        self.assertEqual(payload["configured"]["warnings"], ["automatic paste is unavailable; Cinnamon clipboard copy still works"])

    def test_applet_pipeline_requires_cinnamon_session(self) -> None:
        tools = {"python3", "pw-record", "pactl", "xdotool"}
        env = {"XDG_CURRENT_DESKTOP": "GNOME", "XDG_SESSION_TYPE": "x11", "DESKTOP_SESSION": "gnome"}
        settings = {
            "recorder": "auto",
            "transcriber": "command",
            "transcriber-command": "printf ok",
            "insert-method": "none",
        }
        with mock.patch("speed_of_cinnamon.doctor.shutil.which", which_from(tools)), mock.patch.dict(os.environ, env):
            payload = doctor.report(settings, applet=True)
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["desktop"]["cinnamon"])
        self.assertTrue(payload["configured"]["recorder"]["ok"])
        self.assertTrue(payload["configured"]["transcriber"]["ok"])
        self.assertTrue(payload["configured"]["output"]["ok"])

    def test_cli_clipboard_paste_requires_keyboard_helper(self) -> None:
        tools = {"python3", "pw-record", "pactl", "xsel"}
        env = {"XDG_CURRENT_DESKTOP": "X-Cinnamon", "XDG_SESSION_TYPE": "x11", "DESKTOP_SESSION": "cinnamon"}
        settings = {
            "recorder": "auto",
            "transcriber": "command",
            "transcriber-command": "printf ok",
            "insert-method": "clipboard-paste",
        }
        with mock.patch("speed_of_cinnamon.doctor.shutil.which", which_from(tools)), mock.patch.dict(os.environ, env):
            payload = doctor.report(settings)
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["configured"]["output"]["ok"])
        self.assertIn("xdotool", payload["configured"]["output"]["detail"])

    def test_direct_typing_requires_xdotool_on_x11(self) -> None:
        tools = {"python3", "pw-record", "pactl"}
        env = {"XDG_CURRENT_DESKTOP": "X-Cinnamon", "XDG_SESSION_TYPE": "x11", "DESKTOP_SESSION": "cinnamon"}
        settings = {
            "recorder": "auto",
            "transcriber": "command",
            "transcriber-command": "printf ok",
            "insert-method": "type",
        }
        with mock.patch("speed_of_cinnamon.doctor.shutil.which", which_from(tools)), mock.patch.dict(os.environ, env):
            payload = doctor.report(settings)
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["configured"]["output"]["ok"])
        self.assertIn("xdotool", payload["configured"]["output"]["detail"])

    def test_arecord_is_a_supported_recording_fallback(self) -> None:
        tools = {"python3", "arecord"}
        settings = {
            "recorder": "auto",
            "transcriber": "command",
            "transcriber-command": "printf ok",
            "insert-method": "none",
        }
        with mock.patch("speed_of_cinnamon.doctor.shutil.which", which_from(tools)):
            payload = doctor.report(settings)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["configured"]["recorder"]["ok"])
        self.assertIn("arecord", payload["configured"]["recorder"]["detail"])

    def test_whisper_cpp_requires_existing_model_path(self) -> None:
        tools = {"python3", "pw-record", "whisper-cli"}
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "ggml-base.bin"
            model.write_bytes(b"model")
            settings = {
                "recorder": "auto",
                "transcriber": "whisper-cpp",
                "whisper-model": str(model),
                "insert-method": "none",
            }
            with mock.patch("speed_of_cinnamon.doctor.shutil.which", which_from(tools)):
                payload = doctor.report(settings)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["configured"]["transcriber"]["value"], "whisper-cpp")

    def test_auto_asr_can_use_downloaded_whisper_cpp_model(self) -> None:
        tools = {"python3", "pw-record", "whisper-cli"}
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "ggml-tiny.en.bin"
            model.write_bytes(b"model")
            settings = {
                "recorder": "auto",
                "transcriber": "auto",
                "insert-method": "none",
            }
            with (
                mock.patch("speed_of_cinnamon.doctor.default_ctranslate2_model_path", return_value=""),
                mock.patch("speed_of_cinnamon.doctor.default_whisper_cpp_model_path", return_value=str(model)),
                mock.patch("speed_of_cinnamon.doctor.shutil.which", which_from(tools)),
            ):
                payload = doctor.report(settings)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["configured"]["transcriber"]["resolved"], "whisper-cpp")

    def test_english_only_whisper_cpp_model_fails_for_non_english_language(self) -> None:
        tools = {"python3", "pw-record", "pwcpp"}
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "ggml-tiny.en.bin"
            model.write_bytes(b"model")
            settings = {
                "language": "de",
                "recorder": "auto",
                "transcriber": "whisper-cpp",
                "whisper-model": str(model),
                "insert-method": "none",
            }
            with mock.patch("speed_of_cinnamon.doctor.shutil.which", which_from(tools)):
                payload = doctor.report(settings)
        self.assertFalse(payload["ok"])
        self.assertIn("English-only", payload["configured"]["transcriber"]["detail"])

    def test_auto_asr_accepts_fedora_pwcpp(self) -> None:
        tools = {"python3", "pw-record", "pwcpp"}
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "ggml-tiny.en.bin"
            model.write_bytes(b"model")
            settings = {
                "recorder": "auto",
                "transcriber": "auto",
                "insert-method": "none",
            }
            with (
                mock.patch("speed_of_cinnamon.doctor.default_ctranslate2_model_path", return_value=""),
                mock.patch("speed_of_cinnamon.doctor.default_whisper_cpp_model_path", return_value=str(model)),
                mock.patch("speed_of_cinnamon.doctor.faster_whisper_available", return_value=False),
                mock.patch("speed_of_cinnamon.doctor.shutil.which", which_from(tools)),
            ):
                payload = doctor.report(settings)
        self.assertTrue(payload["ok"])
        check_names = [check["name"] for check in payload["checks"]]
        self.assertIn("pwcpp", check_names)
        self.assertTrue(next((check["ok"] for check in payload["checks"] if check["name"] == "pwcpp"), False))
        self.assertEqual(payload["configured"]["transcriber"]["resolved"], "whisper-cpp")

    def test_auto_asr_accepts_downloaded_ctranslate2_directory_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "ct2-model"
            model.mkdir()
            settings = {
                "recorder": "auto",
                "transcriber": "auto",
                "whisper-model": str(model),
                "insert-method": "none",
            }
            with (
                mock.patch("speed_of_cinnamon.doctor.faster_whisper_available", return_value=True),
                mock.patch("speed_of_cinnamon.doctor.shutil.which", which_from({"python3", "pw-record"})),
            ):
                payload = doctor.report(settings)
        self.assertTrue(payload["configured"]["transcriber"]["ok"])
        self.assertEqual(payload["configured"]["transcriber"]["resolved"], "faster-whisper")

    def test_auto_asr_reports_missing_configured_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "missing.bin"
            settings = {
                "recorder": "auto",
                "transcriber": "auto",
                "whisper-model": str(model),
                "insert-method": "none",
            }
            with mock.patch("speed_of_cinnamon.doctor.shutil.which", which_from({"python3", "pw-record"})):
                payload = doctor.report(settings)
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["configured"]["transcriber"]["ok"])
        self.assertIn("voice model not found", payload["configured"]["transcriber"]["detail"])

    def test_auto_asr_reports_missing_faster_whisper_for_directory_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "ct2-missing-module"
            model.mkdir()
            settings = {
                "recorder": "auto",
                "transcriber": "auto",
                "whisper-model": str(model),
                "insert-method": "none",
            }
            with (
                mock.patch("speed_of_cinnamon.doctor.faster_whisper_available", return_value=False),
                mock.patch("speed_of_cinnamon.doctor.shutil.which", which_from({"python3", "pw-record"})),
            ):
                payload = doctor.report(settings)
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["configured"]["transcriber"]["ok"])
        self.assertIn("faster-whisper is missing", payload["configured"]["transcriber"]["detail"])

    def test_auto_prefers_configured_model_over_whisper_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "ct2-model"
            model.mkdir()
            settings = {
                "recorder": "auto",
                "transcriber": "auto",
                "whisper-model": str(model),
                "insert-method": "none",
            }
            with (
                mock.patch("speed_of_cinnamon.doctor.faster_whisper_available", return_value=True),
                mock.patch("speed_of_cinnamon.doctor.shutil.which", which_from({"python3", "pw-record", "whisper", "whisper-cli"})),
            ):
                payload = doctor.report(settings)
        self.assertEqual(payload["configured"]["transcriber"]["resolved"], "faster-whisper")

    def test_report_treats_openai_whisper_alias_as_whisper(self) -> None:
        tools = {"python3", "pw-record", "whisper"}
        settings = {
            "recorder": "auto",
            "transcriber": "openai-whisper",
            "insert-method": "none",
        }
        with mock.patch("speed_of_cinnamon.doctor.shutil.which", which_from(tools)):
            payload = doctor.report(settings)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["configured"]["transcriber"]["ok"])
        self.assertEqual(payload["configured"]["transcriber"]["value"], "whisper")

    def test_report_treats_custom_alias_as_command(self) -> None:
        settings = {
            "recorder": "auto",
            "transcriber": "custom",
            "transcriber-command": "printf ok",
            "insert-method": "none",
        }
        with mock.patch("speed_of_cinnamon.doctor.shutil.which", which_from({"python3", "pw-record"})):
            payload = doctor.report(settings)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["configured"]["transcriber"]["ok"])
        self.assertEqual(payload["configured"]["transcriber"]["value"], "command")

    def test_report_treats_openai_alias_as_whisper(self) -> None:
        tools = {"python3", "pw-record", "whisper"}
        settings = {
            "recorder": "auto",
            "transcriber": "openai",
            "insert-method": "none",
        }
        with mock.patch("speed_of_cinnamon.doctor.shutil.which", which_from(tools)):
            payload = doctor.report(settings)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["configured"]["transcriber"]["ok"])
        self.assertEqual(payload["configured"]["transcriber"]["value"], "whisper")

    def test_report_treats_template_alias_as_command(self) -> None:
        settings = {
            "recorder": "auto",
            "transcriber": "template",
            "transcriber-command": "printf ok",
            "insert-method": "none",
        }
        with mock.patch("speed_of_cinnamon.doctor.shutil.which", which_from({"python3", "pw-record"})):
            payload = doctor.report(settings)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["configured"]["transcriber"]["ok"])
        self.assertEqual(payload["configured"]["transcriber"]["value"], "command")

    def test_report_treats_template_alias_as_command_and_requires_template(self) -> None:
        settings = {
            "recorder": "auto",
            "transcriber": "template",
            "insert-method": "none",
        }
        with mock.patch("speed_of_cinnamon.doctor.shutil.which", which_from({"python3", "pw-record"})):
            payload = doctor.report(settings)
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["configured"]["transcriber"]["ok"])
        self.assertEqual(payload["configured"]["transcriber"]["value"], "command")
        self.assertEqual(
            payload["configured"]["transcriber"]["detail"],
            "custom transcriber command is empty",
        )

    def test_report_treats_openai_alias_as_whisper_and_reports_missing_binary(self) -> None:
        settings = {
            "recorder": "auto",
            "transcriber": "openai",
            "insert-method": "none",
        }
        with mock.patch("speed_of_cinnamon.doctor.shutil.which", which_from({"python3", "pw-record"})):
            payload = doctor.report(settings)
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["configured"]["transcriber"]["ok"])
        self.assertEqual(payload["configured"]["transcriber"]["value"], "whisper")
        self.assertIn("whisper", payload["configured"]["transcriber"]["detail"])

    def test_external_api_transcriber_requires_model(self) -> None:
        payload = doctor.report({
            "recorder": "auto",
            "transcriber": "openai-compatible",
            "openai-compatible-model": "",
            "insert-method": "none",
        })

        self.assertFalse(payload["configured"]["transcriber"]["ok"])
        self.assertEqual(payload["configured"]["transcriber"]["value"], "openai-compatible")
        self.assertIn("speech model is required", payload["configured"]["transcriber"]["detail"])

    def test_external_api_transcriber_is_ready_when_model_is_configured(self) -> None:
        payload = doctor.report({
            "recorder": "auto",
            "transcriber": "external-api",
            "openai-compatible-model": "whisper-large-v3",
            "openai-compatible-url": "https://api.example.test/v1",
            "insert-method": "none",
        })

        self.assertTrue(payload["configured"]["transcriber"]["ok"])
        self.assertEqual(payload["configured"]["transcriber"]["value"], "openai-compatible")
        self.assertIn("https://api.example.test/v1", payload["configured"]["transcriber"]["detail"])

    def test_ollama_postprocessor_requires_model(self) -> None:
        tools = {"python3", "pw-record"}
        settings = {
            "recorder": "auto",
            "transcriber": "command",
            "transcriber-command": "printf ok",
            "insert-method": "none",
            "post-process-backend": "ollama",
        }
        with mock.patch("speed_of_cinnamon.doctor.shutil.which", which_from(tools)):
            payload = doctor.report(settings)
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["configured"]["postprocessor"]["ok"])
        self.assertIn("Ollama model", payload["configured"]["postprocessor"]["detail"])

    def test_ollama_postprocessor_is_ready_when_model_is_configured(self) -> None:
        tools = {"python3", "pw-record"}
        settings = {
            "recorder": "auto",
            "transcriber": "command",
            "transcriber-command": "printf ok",
            "insert-method": "none",
            "post-process-backend": "ollama",
            "ollama-model": "llama3.2:3b",
        }
        with mock.patch("speed_of_cinnamon.doctor.shutil.which", which_from(tools)):
            payload = doctor.report(settings)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["configured"]["postprocessor"]["value"], "ollama")

    def test_openai_compatible_postprocessor_requires_model(self) -> None:
        tools = {"python3", "pw-record"}
        settings = {
            "recorder": "auto",
            "transcriber": "command",
            "transcriber-command": "printf ok",
            "insert-method": "none",
            "post-process-backend": "openai-compatible",
            "openai-compatible-model": "",
        }
        with mock.patch("speed_of_cinnamon.doctor.shutil.which", which_from(tools)):
            payload = doctor.report(settings)
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["configured"]["postprocessor"]["ok"])
        self.assertIn("OpenAI-compatible text model", payload["configured"]["postprocessor"]["detail"])

    def test_openai_compatible_postprocessor_is_ready_when_model_is_configured(self) -> None:
        tools = {"python3", "pw-record"}
        settings = {
            "recorder": "auto",
            "transcriber": "command",
            "transcriber-command": "printf ok",
            "insert-method": "none",
            "post-process-backend": "openai-compatible",
            "openai-compatible-model": "local-llama",
            "openai-compatible-url": "http://127.0.0.1:8000/v1",
        }
        with mock.patch("speed_of_cinnamon.doctor.shutil.which", which_from(tools)):
            payload = doctor.report(settings)
        self.assertTrue(payload["ok"])

    def test_openai_compatible_postprocessor_uses_separate_text_model_when_configured(self) -> None:
        tools = {"python3", "pw-record"}
        settings = {
            "recorder": "auto",
            "transcriber": "command",
            "transcriber-command": "printf ok",
            "insert-method": "none",
            "post-process-backend": "openai-compatible",
            "openai-compatible-model": "",
            "openai-compatible-text-model": "local-polisher",
            "openai-compatible-url": "http://127.0.0.1:8000/v1",
        }
        with mock.patch("speed_of_cinnamon.doctor.shutil.which", which_from(tools)):
            payload = doctor.report(settings)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["configured"]["postprocessor"]["value"], "openai-compatible")
        self.assertIn("OpenAI-compatible API", payload["configured"]["postprocessor"]["detail"])
        self.assertNotIn("local", payload["configured"]["postprocessor"]["detail"])

    def test_report_rejects_invalid_whisper_model_path(self) -> None:
        tools = {"python3", "pw-record", "whisper-cli"}
        settings = {
            "recorder": "auto",
            "transcriber": "whisper-cpp",
            "whisper-model": "x\x00",
            "insert-method": "none",
        }
        with mock.patch("speed_of_cinnamon.doctor.shutil.which", which_from(tools)):
            payload = doctor.report(settings)
        self.assertFalse(payload["configured"]["transcriber"]["ok"])
        self.assertIn("invalid", payload["configured"]["transcriber"]["detail"])

    def test_report_rejects_escaped_null_in_whisper_model_path(self) -> None:
        tools = {"python3", "pw-record", "whisper-cli"}
        settings = {
            "recorder": "auto",
            "transcriber": "whisper-cpp",
            "whisper-model": "x\\\\x00y",
            "insert-method": "none",
        }
        with mock.patch("speed_of_cinnamon.doctor.shutil.which", which_from(tools)):
            payload = doctor.report(settings)
        self.assertFalse(payload["configured"]["transcriber"]["ok"])
        self.assertIn("invalid", payload["configured"]["transcriber"]["detail"])

    def test_parse_settings_json_rejects_null_byte(self) -> None:
        with self.assertRaisesRegex(ValueError, "contains invalid null byte"):
            doctor.parse_settings_json('{\"language\":\"en\x00\"}')

    def test_parse_settings_json_rejects_escaped_null_byte(self) -> None:
        with self.assertRaisesRegex(ValueError, "contains invalid null byte"):
            doctor.parse_settings_json('{"language":"en\\\\u0000"}')

    def test_parse_settings_json_rejects_escaped_x00_null_byte(self) -> None:
        with self.assertRaisesRegex(ValueError, "contains invalid null byte"):
            doctor.parse_settings_json('{"language":"en\\\\x00"}')

    def test_parse_settings_json_rejects_large_payload(self) -> None:
        with self.assertRaisesRegex(ValueError, "settings JSON is too large"):
            doctor.parse_settings_json(json.dumps({"payload": "x" * (doctor.MAX_SETTINGS_JSON_CHARS + 1)}))

    def test_parse_settings_json_rejects_large_payload_bytes(self) -> None:
        with mock.patch("speed_of_cinnamon.doctor.MAX_SETTINGS_JSON_CHARS", 4):
            with self.assertRaisesRegex(ValueError, "settings JSON is too large"):
                doctor.parse_settings_json('{"payload":"😀"}')

    def test_parse_settings_json_rejects_non_text_payload(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be text"):
            doctor.parse_settings_json({})  # type: ignore[arg-type]

    def test_parse_settings_json_rejects_bool_payload(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be text"):
            doctor.parse_settings_json(True)  # type: ignore[arg-type]

    def test_contains_escaped_null_rejects_non_text(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be text"):
            doctor._contains_escaped_null(123)  # type: ignore[arg-type]

    def test_contains_escaped_null_rejects_bool(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be text"):
            doctor._contains_escaped_null(True)  # type: ignore[arg-type]

    def test_parse_settings_json_rejects_non_object_root(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be an object"):
            doctor.parse_settings_json("[\"en\"]")

    def test_setting_rejects_non_text_payload(self) -> None:
        with self.assertRaisesRegex(ValueError, "setting language must be text"):
            doctor._setting({"language": 1}, "language")  # type: ignore[arg-type]

    def test_setting_rejects_bool_payload(self) -> None:
        with self.assertRaisesRegex(ValueError, "setting language must be text"):
            doctor._setting({"language": True}, "language")  # type: ignore[arg-type]

    def test_ok_rejects_non_check_object(self) -> None:
        self.assertFalse(doctor._ok({"python3": {"ok": True}}, "python3"))  # type: ignore[arg-type]

    def test_output_status_rejects_non_boolean_desktop_flags(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "cinnamon must be a boolean"):
            doctor._output_status(
                {"insert-method": "clipboard"},
                {},
                {"cinnamon": "false", "x11": "true"},
                applet=True,
            )

    def test_output_status_rejects_non_boolean_applet(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "applet must be a boolean"):
            doctor._output_status(
                {"insert-method": "clipboard"},
                {},
                {"cinnamon": True, "x11": False},
                applet="yes",
            )

    def test_report_rejects_non_boolean_desktop_cinnamon_flag(self) -> None:
        def checks_with_python3() -> list[doctor.Check]:
            return [
                doctor.Check(name="python3", ok=True, detail="/usr/bin/python3"),
            ]

        with (
            mock.patch("speed_of_cinnamon.doctor._env_desktop", return_value={"cinnamon": "false"}),
            mock.patch("speed_of_cinnamon.doctor.run_checks", side_effect=checks_with_python3),
            mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "", "XDG_SESSION_TYPE": "", "DESKTOP_SESSION": ""}),
        ):
            with self.assertRaisesRegex(RuntimeError, "cinnamon must be a boolean"):
                doctor.report({}, applet=True)

    def test_report_rejects_non_boolean_applet(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "applet must be a boolean"):
            doctor.report({}, applet="yes")  # type: ignore[arg-type]

    def test_configured_status_rejects_non_boolean_applet(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "applet must be a boolean"):
            doctor.configured_status({}, {}, {"cinnamon": True}, applet="yes")  # type: ignore[arg-type]

    def test_report_rejects_non_boolean_python_check(self) -> None:
        checks = [
            doctor.Check(name="python3", ok="yes", detail="/usr/bin/python3"),  # type: ignore[arg-type]
            doctor.Check(name="arecord", ok=True, detail="/usr/bin/arecord"),
        ]
        with (
            mock.patch("speed_of_cinnamon.doctor.run_checks", return_value=checks),
            mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "", "XDG_SESSION_TYPE": "", "DESKTOP_SESSION": ""}),
        ):
            with self.assertRaisesRegex(RuntimeError, "python3\\.ok must be a boolean"):
                doctor.report(
                    {
                        "recorder": "arecord",
                        "transcriber": "command",
                        "transcriber-command": "printf ok",
                        "insert-method": "none",
                    }
                )

    def test_configured_status_rejects_non_boolean_output_flags_for_warning(self) -> None:
        with mock.patch(
            "speed_of_cinnamon.doctor._output_status",
            return_value={"ok": True, "value": "clipboard-paste", "paste_ok": "false"},
        ):
            with self.assertRaisesRegex(RuntimeError, "paste_ok must be a boolean"):
                doctor.configured_status({"insert-method": "clipboard-paste"}, {}, {"cinnamon": True}, applet=True)

    def test_configured_status_rejects_non_boolean_output_ok_for_warning(self) -> None:
        with mock.patch(
            "speed_of_cinnamon.doctor._output_status",
            return_value={"ok": "yes", "value": "clipboard-paste", "paste_ok": False},
        ):
            with self.assertRaisesRegex(RuntimeError, "ok must be a boolean"):
                doctor.configured_status({"insert-method": "clipboard-paste"}, {}, {"cinnamon": True}, applet=True)

    def test_report_rejects_missing_python3_check(self) -> None:
        checks = [doctor.Check(name="arecord", ok=True, detail="/usr/bin/arecord")]
        with (
            mock.patch("speed_of_cinnamon.doctor.run_checks", return_value=checks),
            mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "", "XDG_SESSION_TYPE": "", "DESKTOP_SESSION": ""}),
        ):
            payload = doctor.report({"recorder": "arecord", "transcriber": "command", "transcriber-command": "printf ok"})
        self.assertFalse(payload["ok"])


if __name__ == "__main__":
    unittest.main()
