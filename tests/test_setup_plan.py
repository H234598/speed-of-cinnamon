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
        self.assertIn("speed-of-cinnamon models --json", plan["commands"])
        self.assertIn("speed-of-cinnamon download-model tiny.en --json", plan["commands"])
        self.assertIn("Install or configure", plan["text"])

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


if __name__ == "__main__":
    unittest.main()
