from __future__ import annotations

import unittest

from speed_of_cinnamon.setup_plan import build_setup_plan


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
                    "detail": "OpenAI-compatible local model is required",
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
