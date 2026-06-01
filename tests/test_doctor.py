from __future__ import annotations

import os
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
        with mock.patch("speed_of_cinnamon.doctor.shutil.which", which_from(tools)), mock.patch.dict(os.environ, env):
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


if __name__ == "__main__":
    unittest.main()
