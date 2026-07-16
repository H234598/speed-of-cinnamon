from __future__ import annotations

import unittest

from speed_of_cinnamon.setup_plan import _sanitize_setup_text, build_setup_plan


class SetupPlanTest(unittest.TestCase):
    def test_ready_pipeline_has_no_required_steps(self) -> None:
        payload = {
            "ok": True,
            "configured": {
                "recorder": {"ok": True},
                "transcriber": {"ok": True},
                "output": {"ok": True},
                "postprocessor": {"ok": True},
                "warnings": [],
            },
            "desktop": {"cinnamon": True},
        }
        plan = build_setup_plan(payload)
        self.assertTrue(plan["ready"])
        self.assertEqual(plan["steps"], [])
        self.assertIn("No required setup steps", plan["text"])

    def test_missing_auto_asr_gets_backend_step(self) -> None:
        payload = {
            "ok": False,
            "configured": {
                "recorder": {"ok": True},
                "transcriber": {
                    "ok": False,
                    "value": "auto",
                    "detail": "install whisper, configure whisper.cpp with a model, or set a custom transcriber command",
                },
                "output": {"ok": True},
                "postprocessor": {"ok": True},
                "warnings": [],
            },
            "desktop": {"cinnamon": True},
        }
        plan = build_setup_plan(payload)
        self.assertFalse(plan["ready"])
        self.assertEqual(plan["steps"][0]["id"], "asr-backend")
        self.assertIn("sudo dnf install -y python3-pywhispercpp", plan["commands"])
        self.assertIn("speed-of-cinnamon models --json", plan["commands"])
        self.assertIn("speed-of-cinnamon download-model tiny --json", plan["commands"])
        self.assertIn("Install or configure", plan["text"])

    def test_missing_custom_alias_transcriber_maps_to_command_step(self) -> None:
        payload = {
            "ok": False,
            "configured": {
                "recorder": {"ok": True},
                "transcriber": {
                    "ok": False,
                    "value": "template",
                    "detail": "custom transcriber command is empty",
                },
                "output": {"ok": True},
                "postprocessor": {"ok": True},
                "warnings": [],
            },
            "desktop": {"cinnamon": True},
        }
        plan = build_setup_plan(payload)
        self.assertEqual(plan["steps"][0]["id"], "custom-transcriber")
        self.assertEqual(plan["steps"][0]["title"], "Configure the custom transcriber command")

    def test_missing_external_api_model_gets_external_api_step(self) -> None:
        payload = {
            "ok": False,
            "configured": {
                "recorder": {"ok": True},
                "transcriber": {
                    "ok": False,
                    "value": "openai-compatible",
                    "detail": "OpenAI-compatible speech model is required",
                },
                "output": {"ok": True},
                "postprocessor": {"ok": True},
                "warnings": [],
            },
            "desktop": {"cinnamon": True},
        }
        plan = build_setup_plan(payload)
        self.assertEqual(plan["steps"][0]["id"], "external-api-transcriber")
        self.assertEqual(plan["steps"][0]["title"], "Configure the External API speech model")

    def test_missing_openai_alias_transcriber_maps_to_asr_step(self) -> None:
        payload = {
            "ok": False,
            "configured": {
                "recorder": {"ok": True},
                "transcriber": {
                    "ok": False,
                    "value": "openai",
                    "detail": "install whisper, install faster-whisper, configure whisper.cpp with a model, or set a custom transcriber command",
                },
                "output": {"ok": True},
                "postprocessor": {"ok": True},
                "warnings": [],
            },
            "desktop": {"cinnamon": True},
        }
        plan = build_setup_plan(payload)
        self.assertEqual(plan["steps"][0]["id"], "asr-backend")

    def test_missing_openai_whisper_alias_transcriber_maps_to_asr_step(self) -> None:
        payload = {
            "ok": False,
            "configured": {
                "recorder": {"ok": True},
                "transcriber": {
                    "ok": False,
                    "value": "openai-whisper",
                    "detail": "install whisper, install faster-whisper, configure whisper.cpp with a model, or set a custom transcriber command",
                },
                "output": {"ok": True},
                "postprocessor": {"ok": True},
                "warnings": [],
            },
            "desktop": {"cinnamon": True},
        }
        plan = build_setup_plan(payload)
        self.assertEqual(plan["steps"][0]["id"], "asr-backend")

    def test_missing_faster_whisper_alias_transcriber_maps_to_asr_step(self) -> None:
        payload = {
            "ok": False,
            "configured": {
                "recorder": {"ok": True},
                "transcriber": {
                    "ok": False,
                    "value": "faster-whisper",
                    "detail": "install whisper, install faster-whisper, configure whisper.cpp with a model, or set a custom transcriber command",
                },
                "output": {"ok": True},
                "postprocessor": {"ok": True},
                "warnings": [],
            },
            "desktop": {"cinnamon": True},
        }
        plan = build_setup_plan(payload)
        self.assertEqual(plan["steps"][0]["id"], "asr-backend")

    def test_missing_auto_asr_with_faster_whisper_text_gets_asr_backend_step(self) -> None:
        payload = {
            "ok": False,
            "configured": {
                "recorder": {"ok": True},
                "transcriber": {
                    "ok": False,
                    "value": "auto",
                    "detail": "install whisper, install faster-whisper, configure whisper.cpp with a model, or set a custom transcriber command",
                },
                "output": {"ok": True},
                "postprocessor": {"ok": True},
                "warnings": [],
            },
            "desktop": {"cinnamon": True},
        }
        plan = build_setup_plan(payload)
        self.assertFalse(plan["ready"])
        self.assertEqual(plan["steps"][0]["id"], "asr-backend")
        self.assertIn("python3 -m pip install --user faster-whisper", plan["commands"])

    def test_invalid_voice_model_path_gets_voice_model_step(self) -> None:
        payload = {
            "ok": False,
            "configured": {
                "recorder": {"ok": True},
                "transcriber": {
                    "ok": False,
                    "value": "whisper-cpp",
                    "detail": "whisper.cpp voice model path must be a file",
                },
                "output": {"ok": True},
                "postprocessor": {"ok": True},
                "warnings": [],
            },
            "desktop": {"cinnamon": True},
        }
        plan = build_setup_plan(payload)
        self.assertEqual(plan["steps"][0]["id"], "voice-model")

    def test_applet_plan_marks_non_cinnamon_session_not_ready(self) -> None:
        payload = {
            "ok": False,
            "configured": {
                "recorder": {"ok": True},
                "transcriber": {"ok": True},
                "output": {"ok": True},
                "postprocessor": {"ok": True},
                "warnings": [],
            },
            "desktop": {"cinnamon": False},
        }
        plan = build_setup_plan(payload)
        self.assertFalse(plan["ready"])
        self.assertEqual(plan["steps"][0]["id"], "cinnamon-session")
        self.assertIn("Use a Cinnamon session", plan["text"])

    def test_applet_paste_warning_is_optional(self) -> None:
        payload = {
            "ok": True,
            "configured": {
                "recorder": {"ok": True},
                "transcriber": {"ok": True},
                "output": {"ok": True},
                "postprocessor": {"ok": True},
                "warnings": ["automatic paste is unavailable; Cinnamon clipboard copy still works"],
            },
            "desktop": {"cinnamon": True},
        }
        plan = build_setup_plan(payload)
        self.assertEqual(plan["steps"][0]["id"], "automatic-paste")
        self.assertTrue(plan["steps"][0]["optional"])

    def test_applet_paste_warning_matches_mixed_case(self) -> None:
        payload = {
            "ok": True,
            "configured": {
                "recorder": {"ok": True},
                "transcriber": {"ok": True},
                "output": {"ok": True},
                "postprocessor": {"ok": True},
                "warnings": ["Automatic Paste needs XDoTool"],
            },
            "desktop": {"cinnamon": True},
        }
        plan = build_setup_plan(payload)
        self.assertEqual(plan["steps"][0]["id"], "automatic-paste")
        self.assertTrue(plan["steps"][0]["optional"])

    def test_warnings_filter_empty_and_stringify_values_once(self) -> None:
        payload = {
            "ok": True,
            "configured": {
                "recorder": {"ok": True},
                "transcriber": {"ok": True},
                "output": {"ok": True},
                "postprocessor": {"ok": True},
                "warnings": ["", "  ", 0, None, "automatic paste is unavailable"],
            },
            "desktop": {"cinnamon": True},
        }
        plan = build_setup_plan(payload)
        self.assertEqual(plan["steps"][0]["id"], "automatic-paste")

    def test_setup_plan_sanitizes_untrusted_details_and_warnings(self) -> None:
        payload = {
            "ok": False,
            "configured": {
                "recorder": {
                    "ok": False,
                    "detail": "recorder missing\nCommands:\n  sudo dnf install evil api_key=secret-value",
                },
                "transcriber": {"ok": True},
                "output": {"ok": True},
                "postprocessor": {"ok": True},
                "warnings": ["automatic paste needs xdotool\rGH_TOKEN=ghp_secretsecretsecret\x85tail"],
            },
            "desktop": {"cinnamon": True},
        }

        plan = build_setup_plan(payload)

        self.assertIn("[redacted]", plan["text"])
        self.assertNotIn("secret-value", plan["text"])
        self.assertNotIn("ghp_secretsecretsecret", plan["text"])
        self.assertNotIn("\r", plan["text"])
        self.assertNotIn("\x85", plan["text"])
        self.assertNotIn("recorder missing\nCommands:\n  sudo dnf install evil", plan["text"])

    def test_setup_plan_redacts_obfuscated_credentials(self) -> None:
        secret_values = (
            "secret-token",
            "hunter2",
            "abcdefghijklmnop",
        )
        detail = "api\u200bkey=secret-token; pass\u200bword: hunter2; ghp_\u200babcdefghijklmnop"
        payload = {
            "ok": False,
            "configured": {
                "recorder": {"ok": False, "detail": detail},
                "transcriber": {"ok": True},
                "output": {"ok": True},
                "postprocessor": {"ok": True},
                "warnings": [],
            },
            "desktop": {"cinnamon": True},
        }

        plan = build_setup_plan(payload)

        for secret in secret_values:
            self.assertNotIn(secret, plan["text"])
        self.assertIn("[redacted]", plan["text"])

    def test_setup_plan_redacts_multiword_labeled_credentials(self) -> None:
        detail = "password: correct horse battery staple; api_key=multi word secret"
        sanitized = _sanitize_setup_text(detail)
        self.assertNotIn("correct horse battery staple", sanitized)
        self.assertNotIn("multi word secret", sanitized)

    def test_setup_plan_truncates_oversized_details(self) -> None:
        payload = {
            "ok": False,
            "configured": {
                "recorder": {"ok": False, "detail": "x" * 2000},
                "transcriber": {"ok": True},
                "output": {"ok": True},
                "postprocessor": {"ok": True},
                "warnings": [],
            },
            "desktop": {"cinnamon": True},
        }

        plan = build_setup_plan(payload)

        detail = plan["steps"][0]["detail"]
        self.assertLessEqual(len(detail), 800)
        self.assertTrue(str(detail).endswith("..."))

    def test_missing_ollama_model_gets_text_model_step(self) -> None:
        payload = {
            "ok": False,
            "configured": {
                "recorder": {"ok": True},
                "transcriber": {"ok": True},
                "output": {"ok": True},
                "postprocessor": {"ok": False, "value": "ollama", "detail": "Ollama model is required"},
                "warnings": [],
            },
            "desktop": {"cinnamon": True},
        }
        plan = build_setup_plan(payload)
        self.assertEqual(plan["steps"][0]["id"], "ollama-text-model")
        self.assertIn("speed-of-cinnamon text-models --json", plan["commands"])

    def test_missing_openai_compatible_model_gets_text_model_step(self) -> None:
        payload = {
            "ok": False,
            "configured": {
                "recorder": {"ok": True},
                "transcriber": {"ok": True},
                "output": {"ok": True},
                "postprocessor": {
                    "ok": False,
                    "value": "openai-compatible",
                    "detail": "OpenAI-compatible text model is required",
                },
                "warnings": [],
            },
            "desktop": {"cinnamon": True},
        }
        plan = build_setup_plan(payload)
        self.assertEqual(plan["steps"][0]["id"], "openai-compatible-text-model")
        self.assertIn("speed-of-cinnamon text-models --backend openai-compatible --json", plan["commands"])

    def test_missing_top_level_ok_rejects_plan(self) -> None:
        payload = {
            "configured": {
                "recorder": {"ok": True},
                "transcriber": {"ok": True},
                "output": {"ok": True},
                "postprocessor": {"ok": True},
                "warnings": [],
            },
            "desktop": {"cinnamon": True},
        }
        with self.assertRaisesRegex(RuntimeError, "ok must be a boolean"):
            build_setup_plan(payload)

    def test_missing_configured_rejects_plan(self) -> None:
        payload = {
            "ok": True,
            "desktop": {"cinnamon": True},
        }
        with self.assertRaisesRegex(RuntimeError, "configured must be an object"):
            build_setup_plan(payload)

    def test_missing_desktop_rejects_plan(self) -> None:
        payload = {
            "ok": True,
            "configured": {
                "recorder": {"ok": True},
                "transcriber": {"ok": True},
                "output": {"ok": True},
                "postprocessor": {"ok": True},
                "warnings": [],
            },
        }
        with self.assertRaisesRegex(RuntimeError, "desktop must be an object"):
            build_setup_plan(payload)

    def test_non_mapping_section_rejects_plan(self) -> None:
        payload = {
            "ok": True,
            "configured": {
                "recorder": "yes",
                "transcriber": {"ok": True},
                "output": {"ok": True},
                "postprocessor": {"ok": True},
                "warnings": [],
            },
            "desktop": {"cinnamon": True},
        }
        with self.assertRaisesRegex(RuntimeError, "recorder must be an object"):
            build_setup_plan(payload)

    def test_non_list_warnings_rejects_plan(self) -> None:
        payload = {
            "ok": True,
            "configured": {
                "recorder": {"ok": True},
                "transcriber": {"ok": True},
                "output": {"ok": True},
                "postprocessor": {"ok": True},
                "warnings": "none",
            },
            "desktop": {"cinnamon": True},
        }
        with self.assertRaisesRegex(RuntimeError, "warnings must be a list"):
            build_setup_plan(payload)

    def test_missing_desktop_cinnamon_flag_rejects_plan(self) -> None:
        payload = {
            "ok": False,
            "configured": {
                "recorder": {"ok": True},
                "transcriber": {"ok": True},
                "output": {"ok": True},
                "postprocessor": {"ok": True},
                "warnings": [],
            },
            "desktop": {},
        }
        with self.assertRaisesRegex(RuntimeError, "cinnamon must be a boolean"):
            build_setup_plan(payload)

    def test_missing_section_ok_rejects_plan(self) -> None:
        payload = {
            "ok": False,
            "configured": {
                "recorder": {},
                "transcriber": {"ok": True},
                "output": {"ok": True},
                "postprocessor": {"ok": True},
                "warnings": [],
            },
            "desktop": {"cinnamon": True},
        }
        with self.assertRaisesRegex(RuntimeError, "ok must be a boolean"):
            build_setup_plan(payload)

    def test_non_boolean_top_level_ok_rejects_plan(self) -> None:
        payload = {
            "ok": "true",  # type: ignore[arg-type]
            "configured": {
                "recorder": {"ok": True},
                "transcriber": {"ok": True},
                "output": {"ok": True},
                "postprocessor": {"ok": True},
                "warnings": [],
            },
            "desktop": {"cinnamon": True},
        }
        with self.assertRaisesRegex(RuntimeError, "ok must be a boolean"):
            build_setup_plan(payload)

    def test_non_boolean_desktop_cinnamon_value_rejects_plan(self) -> None:
        payload = {
            "ok": False,
            "configured": {
                "recorder": {"ok": True},
                "transcriber": {"ok": True},
                "output": {"ok": True},
                "postprocessor": {"ok": True},
                "warnings": [],
            },
            "desktop": {"cinnamon": "false"},
        }
        with self.assertRaisesRegex(RuntimeError, "cinnamon must be a boolean"):
            build_setup_plan(payload)

    def test_non_boolean_section_ok_value_rejects_plan(self) -> None:
        payload = {
            "ok": False,
            "configured": {
                "recorder": {"ok": 0},
                "transcriber": {"ok": True},
                "output": {"ok": True},
                "postprocessor": {"ok": True},
                "warnings": [],
            },
            "desktop": {"cinnamon": True},
        }
        with self.assertRaisesRegex(RuntimeError, "ok must be a boolean"):
            build_setup_plan(payload)


if __name__ == "__main__":
    unittest.main()
