import tempfile
import unittest
from pathlib import Path
from unittest import mock

from speed_of_cinnamon.transcriber import (
    TranscriptionError,
    _openai_compatible_error_detail,
    transcribe_with_openai_compatible_api,
)


class TranscriberErrorDetailTest(unittest.TestCase):
    def test_deeply_nested_json_error_is_returned_as_bounded_raw_detail(self):
        raw = "[" * 1200 + "]" * 1200
        self.assertEqual(_openai_compatible_error_detail(raw), raw)

    def test_deeply_nested_json_response_is_reported_as_invalid_json(self):
        class Response:
            def __init__(self, body: bytes) -> None:
                self._body = body
                self._read = False

            def __enter__(self):
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self, size: int = -1) -> bytes:
                if self._read:
                    return b""
                self._read = True
                return self._body

        raw = "[" * 64000 + "]" * 64000
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"audio")
            with mock.patch(
                "speed_of_cinnamon.transcriber._open_http_request",
                return_value=Response(raw.encode("utf-8")),
            ):
                with self.assertRaisesRegex(TranscriptionError, "returned invalid JSON"):
                    transcribe_with_openai_compatible_api(
                        audio,
                        "en",
                        Path(tmp) / "sample.txt",
                        model="local-transcriber",
                        url="http://127.0.0.1:8000/v1",
                    )


if __name__ == "__main__":
    unittest.main()
