from __future__ import annotations

import hashlib
import io
import os
import tempfile
import unittest
from unittest import mock

from speed_of_cinnamon import models


class FakeResponse:
    def __init__(self, data: bytes) -> None:
        self.buffer = io.BytesIO(data)

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self.buffer.read(size)


class ModelsTest(unittest.TestCase):
    def test_list_models_reports_catalog_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}):
            payload = models.list_models()
        self.assertGreater(len(payload), 0)
        self.assertEqual(payload[0]["name"], "tiny.en")
        self.assertIn("speed-of-cinnamon/models/whisper.cpp/ggml-tiny.en.bin", payload[0]["path"])
        self.assertFalse(payload[0]["downloaded"])

    def test_download_model_writes_verified_file_atomically(self) -> None:
        data = b"tiny model"
        spec = models.ModelSpec(
            name="test",
            filename="ggml-test.bin",
            size="1 KiB",
            sha1=hashlib.sha1(data).hexdigest(),
            description="test model",
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "CATALOG", (spec,)),
            mock.patch("speed_of_cinnamon.models.urllib.request.urlopen", return_value=FakeResponse(data)),
        ):
            payload = models.download_model("test")
            second_payload = models.download_model("test")
        self.assertEqual(payload["status"], "done")
        self.assertTrue(payload["verified"])
        self.assertEqual(payload["checksum"], spec.sha1)
        self.assertIn("already downloaded", second_payload["message"])

    def test_default_model_path_uses_only_verified_catalog_files(self) -> None:
        good_data = b"good model"
        spec = models.ModelSpec(
            name="good",
            filename="ggml-good.bin",
            size="1 KiB",
            sha1=hashlib.sha1(good_data).hexdigest(),
            description="good model",
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "CATALOG", (spec,)),
        ):
            path = models.model_path(spec)
            path.parent.mkdir(parents=True)
            path.write_bytes(b"bad model")
            self.assertEqual(models.default_whisper_cpp_model_path(), "")
            path.write_bytes(good_data)
            self.assertEqual(models.default_whisper_cpp_model_path(), str(path))

    def test_unknown_model_raises_clear_error(self) -> None:
        with self.assertRaisesRegex(models.ModelError, "unknown model"):
            models.resolve_model("missing")


if __name__ == "__main__":
    unittest.main()
