from __future__ import annotations

import json
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


class FakeResponseWithLength:
    def __init__(self, data: bytes, content_length: int) -> None:
        self.buffer = io.BytesIO(data)
        self.headers = {"Content-Length": str(content_length)}

    def __enter__(self) -> "FakeResponseWithLength":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self.buffer.read(size)


class ModelsTest(unittest.TestCase):
    def test_sha1_file_uses_cached_checksum(self) -> None:
        spec = models.ModelSpec(
            name="cached",
            filename="ggml-cached.bin",
            size="1 KiB",
            sha1=hashlib.sha1(b"cached model").hexdigest(),
            description="cached model",
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "_model_checksum_cache", {}),
            mock.patch.object(models, "_model_checksum_cache_loaded", False),
        ):
            path = models.model_path(spec)
            path.parent.mkdir(parents=True)
            path.write_bytes(b"cached model")
            cache_path = models._model_checksum_cache_path()

            with mock.patch.object(models.hashlib, "sha1", wraps=models.hashlib.sha1) as sha1_ctor:
                first = models.sha1_file(path)
                second = models.sha1_file(path)

            self.assertEqual(first, second)
            self.assertEqual(first, spec.sha1)
            self.assertEqual(sha1_ctor.call_count, 1)
            self.assertTrue(cache_path.exists())
            self.assertIn(spec.sha1, cache_path.read_text(encoding="utf-8"))

    def test_model_checksum_cache_recovers_from_invalid_json(self) -> None:
        data = b"cached model"
        spec = models.ModelSpec(
            name="cached-invalid-json",
            filename="ggml-invalid-json.bin",
            size="1 KiB",
            sha1=hashlib.sha1(data).hexdigest(),
            description="invalid json cache",
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "_model_checksum_cache", {}),
            mock.patch.object(models, "_model_checksum_cache_loaded", False),
        ):
            path = models.model_path(spec)
            path.parent.mkdir(parents=True)
            path.write_bytes(data)
            cache_path = models._model_checksum_cache_path()
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text("{not-json", encoding="utf-8")

            checksum = models.sha1_file(path)
            self.assertEqual(checksum, spec.sha1)
            self.assertTrue(cache_path.exists())
            self.assertIn(spec.sha1, cache_path.read_text(encoding="utf-8"))

    def test_model_checksum_cache_prunes_stale_entries(self) -> None:
        spec = models.ModelSpec(
            name="cache-prune",
            filename="ggml-cache-prune.bin",
            size="1 KiB",
            sha1=hashlib.sha1(b"prune model").hexdigest(),
            description="cache prune",
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "_model_checksum_cache", {}),
            mock.patch.object(models, "_model_checksum_cache_loaded", False),
        ):
            cache_path = models._model_checksum_cache_path()
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            path = models.model_path(spec)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"prune model")
            valid_meta = path.stat()

            cache_payload = {
                str(path): {
                    "checksum": spec.sha1,
                    "size": valid_meta.st_size,
                    "mtime_ns": valid_meta.st_mtime_ns,
                },
                "/does/not/exist.bin": {
                    "checksum": "deadbeef",
                    "size": 123,
                    "mtime_ns": 456,
                },
                str(path.with_name("bad.bin")): {
                    "checksum": "beef",
                    "size": "nope",
                    "mtime_ns": "also-nope",
                },
            }
            cache_path.write_text(json.dumps(cache_payload), encoding="utf-8")
            models._load_model_checksum_cache()

            self.assertIn(str(path), models._model_checksum_cache)
            self.assertNotIn("/does/not/exist.bin", models._model_checksum_cache)
            self.assertNotIn(str(path.with_name("bad.bin")), models._model_checksum_cache)

    def test_remove_model_clears_checksum_cache(self) -> None:
        spec = models.ModelSpec(
            name="cache-clear",
            filename="ggml-cache-clear.bin",
            size="1 KiB",
            sha1=hashlib.sha1(b"cache clear").hexdigest(),
            description="cache clear",
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "_model_checksum_cache", {}),
            mock.patch.object(models, "_model_checksum_cache_loaded", False),
            mock.patch.object(models, "CATALOG", (spec,)),
        ):
            path = models.model_path(spec)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"cache clear")
            models.sha1_file(path)
            self.assertIn(str(path), models._model_checksum_cache)
            payload = models.remove_model("cache-clear")
            self.assertNotIn(str(path), models._model_checksum_cache)
            self.assertTrue(payload["removed"])

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

    def test_remove_model_deletes_catalog_file_and_tmp_file(self) -> None:
        spec = models.ModelSpec(
            name="test",
            filename="ggml-test.bin",
            size="1 KiB",
            sha1="not-used",
            description="test model",
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "CATALOG", (spec,)),
        ):
            path = models.model_path(spec)
            tmp_path = path.with_suffix(path.suffix + ".tmp")
            path.parent.mkdir(parents=True)
            path.write_bytes(b"model")
            tmp_path.write_bytes(b"partial")
            payload = models.remove_model("test")
            missing_payload = models.remove_model("test")
            path_exists = path.exists()
            tmp_exists = tmp_path.exists()
        self.assertTrue(payload["removed"])
        self.assertTrue(payload["removed_tmp"])
        self.assertFalse(path_exists)
        self.assertFalse(tmp_exists)
        self.assertFalse(missing_payload["removed"])

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

    def test_download_model_rejects_downloads_over_limit(self) -> None:
        spec = models.ModelSpec(
            name="big",
            filename="ggml-big.bin",
            size="1 MiB",
            sha1=hashlib.sha1(b"x").hexdigest(),
            description="too-big model",
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "CATALOG", (spec,)),
            mock.patch.object(models, "MAX_MODEL_DOWNLOAD_BYTES", 2_000),
            mock.patch(
                "speed_of_cinnamon.models.urllib.request.urlopen",
                return_value=FakeResponse(b"x" * 5_000),
            ),
        ):
            with self.assertRaisesRegex(models.ModelError, "too large"):
                models.download_model("big")

    def test_download_model_rejects_content_length_mismatch(self) -> None:
        data = b"model"
        expected_checksum = hashlib.sha1(b"model").hexdigest()
        spec = models.ModelSpec(
            name="mismatch",
            filename="ggml-mismatch.bin",
            size="1 MiB",
            sha1=expected_checksum,
            description="mismatch model",
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "CATALOG", (spec,)),
            mock.patch.object(models, "MAX_MODEL_DOWNLOAD_BYTES", 10_000),
            mock.patch(
                "speed_of_cinnamon.models.urllib.request.urlopen",
                return_value=FakeResponseWithLength(data=data, content_length=10),
            ),
        ):
            with self.assertRaisesRegex(models.ModelError, "size mismatch"):
                models.download_model("mismatch")

    @mock.patch("speed_of_cinnamon.models.os.replace")
    def test_download_model_raises_model_error_when_atomic_replace_fails(self, mocked_replace: mock.Mock) -> None:
        data = b"tiny model"
        spec = models.ModelSpec(
            name="test",
            filename="ggml-test.bin",
            size="1 KiB",
            sha1=hashlib.sha1(data).hexdigest(),
            description="test model",
        )
        mocked_replace.side_effect = OSError("disk full")
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "CATALOG", (spec,)),
            mock.patch("speed_of_cinnamon.models.urllib.request.urlopen", return_value=FakeResponse(data)),
        ):
            with self.assertRaisesRegex(models.ModelError, "failed to persist downloaded model file"):
                models.download_model("test")
        path = models.model_path(spec)
        self.assertFalse(path.exists())
        self.assertFalse(path.with_suffix(path.suffix + ".tmp").exists())


if __name__ == "__main__":
    unittest.main()
