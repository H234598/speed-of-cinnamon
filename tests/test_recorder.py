from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from speed_of_cinnamon.recorder import choose_recorder, normalize_input_device, parse_pactl_sources


PACTL_SOURCES = """Source #10
\tState: SUSPENDED
\tName: alsa_output.pci-speakers.monitor
\tDescription: Monitor of Speakers
\tDriver: PipeWire
\tMonitor of Sink: alsa_output.pci-speakers

Source #11
\tState: RUNNING
\tName: alsa_input.usb-mic.analog-stereo
\tDescription: USB Microphone
\tDriver: PipeWire
\tMonitor of Sink: n/a
"""


def which_only(command: str) -> mock.Mock:
    return mock.Mock(side_effect=lambda name: f"/usr/bin/{command}" if name == command else None)


class RecorderTest(unittest.TestCase):
    def test_default_input_device_is_normalized_to_empty(self) -> None:
        self.assertEqual(normalize_input_device(""), "")
        self.assertEqual(normalize_input_device("default"), "")
        self.assertEqual(normalize_input_device("@DEFAULT_SOURCE@"), "")
        self.assertEqual(normalize_input_device("alsa_input.usb-mic"), "alsa_input.usb-mic")

    def test_choose_pw_record_adds_target_before_output_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio_path = Path(tmp) / "sample.wav"
            with mock.patch("speed_of_cinnamon.recorder.shutil.which", which_only("pw-record")):
                command = choose_recorder("pw-record", audio_path, 3, "alsa_input.usb-mic")
        self.assertEqual(command.name, "pw-record")
        self.assertIn("--target", command.argv)
        self.assertEqual(command.argv[command.argv.index("--target") + 1], "alsa_input.usb-mic")
        self.assertEqual(command.argv[-1], str(audio_path))

    def test_choose_parecord_adds_device_before_output_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio_path = Path(tmp) / "sample.wav"
            with mock.patch("speed_of_cinnamon.recorder.shutil.which", which_only("parecord")):
                command = choose_recorder("parecord", audio_path, 3, "alsa_input.usb-mic")
        self.assertEqual(command.name, "parecord")
        self.assertIn("--device=alsa_input.usb-mic", command.argv)
        self.assertEqual(command.argv[-1], str(audio_path))

    def test_parse_pactl_sources_filters_monitors_and_marks_default(self) -> None:
        sources = parse_pactl_sources(PACTL_SOURCES, "alsa_input.usb-mic.analog-stereo")
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].id, "11")
        self.assertEqual(sources[0].name, "alsa_input.usb-mic.analog-stereo")
        self.assertEqual(sources[0].description, "USB Microphone")
        self.assertTrue(sources[0].default)
        self.assertFalse(sources[0].monitor)

    def test_parse_pactl_sources_can_include_monitors(self) -> None:
        sources = parse_pactl_sources(PACTL_SOURCES, include_monitors=True)
        self.assertEqual([source.id for source in sources], ["10", "11"])
        self.assertTrue(sources[0].monitor)


if __name__ == "__main__":
    unittest.main()
