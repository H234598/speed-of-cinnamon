from __future__ import annotations

import json
import hashlib
import io
import os
import tempfile
import unittest
import urllib.error
from unittest import mock
from pathlib import Path

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


class FakeRedirectResponse(FakeResponseWithLength):
    def __init__(self, data: bytes, content_length: int, final_url: str) -> None:
        super().__init__(data, content_length)
        self._final_url = final_url

    def geturl(self) -> str:
        return self._final_url


def fake_redirect(url: str, location: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, 302, "Found", {"Location": location}, None)


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

            with (
                mock.patch.object(models.hashlib, "sha1", wraps=models.hashlib.sha1) as sha1_ctor,
                mock.patch("speed_of_cinnamon.path_safety.os.open", wraps=os.open) as mocked_open,
            ):
                first = models.sha1_file(path)
                models._model_checksum_cache_loaded = False
                models._load_model_checksum_cache()
                second = models.sha1_file(path)

            self.assertEqual(first, second)
            self.assertEqual(first, spec.sha1)
            self.assertEqual(sha1_ctor.call_count, 1)
            self.assertTrue(cache_path.exists())
            self.assertTrue(
                any(
                    Path(args[0]) == path and isinstance(args[1], int) and args[1] & os.O_NOFOLLOW
                    for args, _ in mocked_open.call_args_list
                )
            )
            self.assertTrue(
                any(
                    args[0] == cache_path.name
                    and isinstance(args[1], int)
                    and args[1] & os.O_NOFOLLOW
                    and "dir_fd" in kwargs
                    for args, kwargs in mocked_open.call_args_list
                )
            )
            self.assertIn(spec.sha1, cache_path.read_text(encoding="utf-8"))

    def test_model_status_verify_recomputes_checksum_without_cache(self) -> None:
        data = b"verification content"
        spec = models.ModelSpec(
            name="status-verify-fresh-checksum",
            filename="ggml-status-verify-fresh.bin",
            size="1 KiB",
            sha1=hashlib.sha1(data).hexdigest(),
            description="verify bypass cache",
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
            cache_path.write_text(
                json.dumps(
                    {
                        str(path): {
                            "checksum": hashlib.sha1(b"stale").hexdigest(),
                            "size": len(data),
                            "mtime_ns": path.stat().st_mtime_ns,
                        }
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch.object(models, "_cached_or_computed_sha1", side_effect=AssertionError("cache path/metadata should be bypassed")):
                status = models.model_status(spec, verify=True)

            self.assertTrue(status["verified"])
            self.assertEqual(status["checksum"], spec.sha1)

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

    def test_model_checksum_cache_prunes_symlink_entries(self) -> None:
        data = b"cached model"
        spec = models.ModelSpec(
            name="cache-prune-symlink",
            filename="ggml-cache-prune-symlink.bin",
            size="1 KiB",
            sha1=hashlib.sha1(data).hexdigest(),
            description="cache prune symlink",
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "_model_checksum_cache", {}),
            mock.patch.object(models, "_model_checksum_cache_loaded", False),
        ):
            path = models.model_path(spec)
            target = path.parent / "target.bin"
            path.parent.mkdir(parents=True)
            target.write_bytes(data)
            path.symlink_to(target)
            cache_path = models._model_checksum_cache_path()
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps({str(path): {"checksum": spec.sha1, "size": len(data), "mtime_ns": 1}}),
                encoding="utf-8",
            )

            models._load_model_checksum_cache()

            self.assertNotIn(str(path), models._model_checksum_cache)

    def test_model_checksum_cache_rejects_invalid_checksum_entries(self) -> None:
        spec = models.ModelSpec(
            name="cache-invalid-checksum",
            filename="ggml-invalid-checksum.bin",
            size="1 KiB",
            sha1=hashlib.sha1(b"invalid checksum").hexdigest(),
            description="invalid checksum cache",
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
            path.write_bytes(b"invalid checksum")
            cache_payload = {
                str(path): {
                    "checksum": "bad",
                    "size": 1,
                    "mtime_ns": 2,
                }
            }
            cache_path.write_text(json.dumps(cache_payload), encoding="utf-8")
            models._load_model_checksum_cache()
            self.assertNotIn(str(path), models._model_checksum_cache)

    def test_model_checksum_cache_rejects_negative_metadata_entries(self) -> None:
        spec = models.ModelSpec(
            name="cache-negative-meta",
            filename="ggml-negative.bin",
            size="1 KiB",
            sha1=hashlib.sha1(b"negative meta").hexdigest(),
            description="negative checksum cache metadata",
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
            path.write_bytes(b"negative meta")
            cache_payload = {
                str(path): {
                    "checksum": hashlib.sha1(b"negative meta").hexdigest(),
                    "size": -1,
                    "mtime_ns": -1,
                }
            }
            cache_path.write_text(json.dumps(cache_payload), encoding="utf-8")
            models._load_model_checksum_cache()
            self.assertNotIn(str(path), models._model_checksum_cache)

    def test_model_checksum_cache_rejects_oversized_path_entries(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "_model_checksum_cache", {}),
            mock.patch.object(models, "_model_checksum_cache_loaded", False),
        ):
            cache_path = models._model_checksum_cache_path()
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            key = "x" * (models.MAX_MODEL_CHECKSUM_PATH_CHARS + 1)
            cache_path.write_text(
                json.dumps({key: {"checksum": "a" * 40, "size": 1, "mtime_ns": 1}}),
                encoding="utf-8",
            )
            models._load_model_checksum_cache()
            self.assertNotIn(key, models._model_checksum_cache)

    def test_model_checksum_cache_rejects_oversized_path_entries_bytes(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "_model_checksum_cache", {}),
            mock.patch.object(models, "_model_checksum_cache_loaded", False),
        ):
            cache_path = models._model_checksum_cache_path()
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            key = "😀" * ((models.MAX_MODEL_CHECKSUM_PATH_CHARS // 4) + 1)
            cache_path.write_text(
                json.dumps({key: {"checksum": "a" * 40, "size": 1, "mtime_ns": 1}}),
                encoding="utf-8",
            )
            models._load_model_checksum_cache()
            self.assertNotIn(key, models._model_checksum_cache)

    def test_model_checksum_cache_rejects_null_byte_paths(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "_model_checksum_cache", {}),
            mock.patch.object(models, "_model_checksum_cache_loaded", False),
        ):
            cache_path = models._model_checksum_cache_path()
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps({"a\x00b.bin": {"checksum": "a" * 40, "size": 1, "mtime_ns": 1}}),
                encoding="utf-8",
            )
            models._load_model_checksum_cache()
            self.assertEqual(models._model_checksum_cache, {})

    def test_model_checksum_cache_rejects_escaped_null_paths(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "_model_checksum_cache", {}),
            mock.patch.object(models, "_model_checksum_cache_loaded", False),
        ):
            cache_path = models._model_checksum_cache_path()
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps({"a\\\\x00b.bin": {"checksum": "a" * 40, "size": 1, "mtime_ns": 1}}),
                encoding="utf-8",
            )
            models._load_model_checksum_cache()
            self.assertEqual(models._model_checksum_cache, {})

    def test_write_model_checksum_cache_rejects_oversized_payload(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "_model_checksum_cache", {}),
            mock.patch.object(models, "_model_checksum_cache_loaded", False),
        ):
            cache_path = models._model_checksum_cache_path()
            for idx in range(25000):
                models._model_checksum_cache[f"path-{idx}.bin"] = {
                    "checksum": "a" * 40,
                    "size": idx,
                    "mtime_ns": idx,
                }
            models._write_model_checksum_cache()
            self.assertFalse(cache_path.exists())

    def test_write_model_checksum_cache_overflow_clears_cache(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "_model_checksum_cache", {}),
            mock.patch.object(models, "_model_checksum_cache_loaded", False),
        ):
            cache_path = models._model_checksum_cache_path()
            for idx in range(25000):
                models._model_checksum_cache[f"path-{idx}.bin"] = {
                    "checksum": "a" * 40,
                    "size": idx,
                    "mtime_ns": idx,
                }
            models._write_model_checksum_cache()
            self.assertEqual(models._model_checksum_cache, {})
            self.assertFalse(cache_path.exists())

    def test_write_model_checksum_cache_rejects_symlink_parent_after_path_resolution(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(models, "_model_checksum_cache", {}),
            mock.patch.object(models, "_model_checksum_cache_loaded", False),
        ):
            root = Path(tmp)
            real = root / "real"
            real.mkdir()
            link = root / "link"
            link.symlink_to(real, target_is_directory=True)
            cache_path = link / "model_checksums.json"
            models._model_checksum_cache["model.bin"] = {
                "checksum": "a" * 40,
                "size": 1,
                "mtime_ns": 1,
            }

            with mock.patch.object(models, "_model_checksum_cache_path", return_value=cache_path):
                models._write_model_checksum_cache()

            self.assertFalse((real / "model_checksums.json").exists())

    def test_set_model_checksum_cache_rejects_invalid_checksum(self) -> None:
        stat = os.stat_result((0, 0, 0, 0, 0, 0, 12, 0, 0, 0))
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
        ):
            path = models._model_checksum_cache_path().parent / "ggml-invalid.bin"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"invalid checksum")
            with self.assertRaisesRegex(models.ModelError, "invalid model checksum cache state"):
                models._set_model_checksum_cache(path, "bad", stat)

    def test_set_model_checksum_cache_rejects_boolean_size(self) -> None:
        stat = os.stat_result((0, 0, 0, 0, 0, 0, True, 0, 0, 0))
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
        ):
            path = models._model_checksum_cache_path().parent / "ggml-boolean-size.bin"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"boolean")
            with self.assertRaisesRegex(models.ModelError, "invalid model checksum cache state"):
                models._set_model_checksum_cache(path, hashlib.sha1(b"boolean").hexdigest(), stat)

    def test_load_model_checksum_cache_rejects_boolean_entries(self) -> None:
        spec = models.ModelSpec(
            name="cache-boolean",
            filename="ggml-boolean.bin",
            size="1 KiB",
            sha1=hashlib.sha1(b"boolean").hexdigest(),
            description="boolean metadata cache",
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "_model_checksum_cache", {}),
            mock.patch.object(models, "_model_checksum_cache_loaded", False),
        ):
            path = models._model_checksum_cache_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            model_path = models.model_path(spec)
            model_path.parent.mkdir(parents=True, exist_ok=True)
            model_path.write_bytes(b"boolean")
            payload = {
                str(model_path): {"checksum": spec.sha1, "size": True, "mtime_ns": 123},
                "other.bin": {"checksum": spec.sha1, "size": 123, "mtime_ns": False},
            }
            path.write_text(json.dumps(payload), encoding="utf-8")
            models._load_model_checksum_cache()
            self.assertEqual(models._model_checksum_cache, {})

    def test_set_model_checksum_cache_rejects_invalid_path(self) -> None:
        stat = os.stat_result((0, 0, 0, 0, 0, 0, 12, 0, 0, 0))
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
        ):
            path = models._model_checksum_cache_path().parent / "a\x00b.bin"
            with self.assertRaisesRegex(models.ModelError, "invalid model checksum cache state"):
                models._set_model_checksum_cache(path, "a" * 40, stat)

    def test_set_model_checksum_cache_rejects_oversized_byte_path(self) -> None:
        stat = os.stat_result((0, 0, 0, 0, 0, 0, 12, 0, 0, 0))
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
        ):
            key = "😀" * ((models.MAX_MODEL_CHECKSUM_PATH_CHARS // 4) + 1)
            path = models._model_checksum_cache_path().parent / key
            with self.assertRaisesRegex(models.ModelError, "invalid model checksum cache state"):
                models._set_model_checksum_cache(path, "a" * 40, stat)

    def test_model_checksum_cache_rejects_oversized_file(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "_model_checksum_cache", {}),
            mock.patch.object(models, "_model_checksum_cache_loaded", False),
        ):
            cache_path = models._model_checksum_cache_path()
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text("x" * (models.MAX_MODEL_CHECKSUM_JSON_BYTES + 1), encoding="utf-8")
            models._load_model_checksum_cache()
            self.assertFalse(cache_path.exists())

    def test_model_checksum_cache_rejects_invalid_utf8(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "_model_checksum_cache", {}),
            mock.patch.object(models, "_model_checksum_cache_loaded", False),
        ):
            cache_path = models._model_checksum_cache_path()
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(b"\xff")
            models._load_model_checksum_cache()
            self.assertFalse(cache_path.exists())
            self.assertEqual(models._model_checksum_cache, {})

    def test_model_checksum_cache_rejects_escaped_x00_paths(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "_model_checksum_cache", {}),
            mock.patch.object(models, "_model_checksum_cache_loaded", False),
        ):
            cache_path = models._model_checksum_cache_path()
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps({"a\\\\x00b.bin": {"checksum": "a" * 40, "size": 1, "mtime_ns": 1}}),
                encoding="utf-8",
            )
            models._load_model_checksum_cache()
            self.assertEqual(models._model_checksum_cache, {})

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

    def test_remove_model_preserves_checksum_cache_when_delete_fails(self) -> None:
        spec = models.ModelSpec(
            name="cache-delete-fails",
            filename="ggml-cache-delete-fails.bin",
            size="1 KiB",
            sha1=hashlib.sha1(b"cache delete fails").hexdigest(),
            description="cache delete fails",
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "_model_checksum_cache", {}),
            mock.patch.object(models, "_model_checksum_cache_loaded", True),
            mock.patch.object(models, "CATALOG", (spec,)),
        ):
            path = models.model_path(spec)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"cache delete fails")
            models._set_model_checksum_cache(path, spec.sha1, path.stat())
            with mock.patch("speed_of_cinnamon.models.Path.unlink", side_effect=OSError("delete failed")):
                with self.assertRaises(OSError):
                    models.remove_model("cache-delete-fails")
            self.assertIn(str(path), models._model_checksum_cache)

    def test_remove_model_clears_checksum_cache_when_tmp_delete_fails_after_main_delete(self) -> None:
        spec = models.ModelSpec(
            name="cache-tmp-delete-fails",
            filename="ggml-cache-tmp-delete-fails.bin",
            size="1 KiB",
            sha1=hashlib.sha1(b"cache tmp delete fails").hexdigest(),
            description="cache tmp delete fails",
        )
        real_unlink = Path.unlink
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "_model_checksum_cache", {}),
            mock.patch.object(models, "_model_checksum_cache_loaded", True),
            mock.patch.object(models, "CATALOG", (spec,)),
        ):
            path = models.model_path(spec)
            tmp_path = path.with_suffix(path.suffix + ".tmp")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"cache tmp delete fails")
            tmp_path.write_bytes(b"tmp")
            models._set_model_checksum_cache(path, spec.sha1, path.stat())

            def unlink_or_fail(target: Path) -> None:
                if target == tmp_path:
                    raise OSError("tmp delete failed")
                real_unlink(target)

            with mock.patch("speed_of_cinnamon.models.Path.unlink", autospec=True, side_effect=unlink_or_fail):
                with self.assertRaisesRegex(models.ModelError, "failed to remove temporary model file"):
                    models.remove_model("cache-tmp-delete-fails")

            self.assertFalse(path.exists())
            self.assertNotIn(str(path), models._model_checksum_cache)

    def test_remove_model_tmp_delete_failure_survives_cache_clear_failure(self) -> None:
        spec = models.ModelSpec(
            name="cache-tmp-delete-and-clear-fail",
            filename="ggml-cache-tmp-delete-and-clear-fail.bin",
            size="1 KiB",
            sha1=hashlib.sha1(b"cache tmp delete and clear fail").hexdigest(),
            description="cache tmp delete and clear fail",
        )
        real_unlink = Path.unlink
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "_model_checksum_cache", {}),
            mock.patch.object(models, "_model_checksum_cache_loaded", True),
            mock.patch.object(models, "CATALOG", (spec,)),
        ):
            path = models.model_path(spec)
            tmp_path = path.with_suffix(path.suffix + ".tmp")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"cache tmp delete and clear fail")
            tmp_path.write_bytes(b"tmp")
            models._set_model_checksum_cache(path, spec.sha1, path.stat())

            def unlink_or_fail(target: Path) -> None:
                if target == tmp_path:
                    raise OSError("tmp delete failed")
                real_unlink(target)

            with (
                mock.patch("speed_of_cinnamon.models.Path.unlink", autospec=True, side_effect=unlink_or_fail),
                mock.patch(
                    "speed_of_cinnamon.models._clear_model_checksum_cache",
                    side_effect=models.ModelError("cache clear failed"),
                ),
            ):
                with self.assertRaisesRegex(models.ModelError, "failed to remove temporary model file"):
                    models.remove_model("cache-tmp-delete-and-clear-fail")

            self.assertFalse(path.exists())

    def test_list_models_reports_catalog_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}):
            payload = models.list_models()
        self.assertGreater(len(payload), 0)
        self.assertEqual(payload[0]["name"], "tiny.en")
        self.assertIn("speed-of-cinnamon/models/whisper.cpp/ggml-tiny.en.bin", payload[0]["path"])
        self.assertFalse(payload[0]["downloaded"])

    def test_catalog_includes_german_tiny_model(self) -> None:
        spec = models.resolve_model("tiny-de")
        self.assertEqual(spec.filename, "ggml-tiny-de.bin")
        self.assertEqual(spec.sha1, "d69d0a00ed0ab978e22faf86c73960cb6ed21b25")
        self.assertEqual(spec.languages, ("de",))
        self.assertIn("wabisabisocial/whisper-tiny-german-ggml", spec.url)
        self.assertTrue(models.model_supports_language("ggml-tiny-de.bin", "de-DE"))
        self.assertFalse(models.model_supports_language("ggml-tiny-de.bin", "en"))

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
            mock.patch("speed_of_cinnamon.models._open_model_download_url", return_value=FakeResponse(data)),
        ):
            payload = models.download_model("test")
            second_payload = models.download_model("test")
        self.assertEqual(payload["status"], "done")
        self.assertTrue(payload["verified"])
        self.assertEqual(payload["checksum"], spec.sha1)
        self.assertIn("already downloaded", second_payload["message"])

    def test_download_model_rejects_target_symlink_before_final_replace(self) -> None:
        data = b"tiny model"
        spec = models.ModelSpec(
            name="test-target-race",
            filename="ggml-test-target-race.bin",
            size="1 KiB",
            sha1=hashlib.sha1(data).hexdigest(),
            description="target symlink race test",
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "CATALOG", (spec,)),
            mock.patch("speed_of_cinnamon.models._open_model_download_url", return_value=FakeResponse(data)),
        ):
            path = models.model_path(spec)
            path.parent.mkdir(parents=True)
            marker = Path(tmp) / "model-target-race-marker"
            called = {"armed": False}
            original = models.assert_no_symlink_ancestors

            def assert_no_with_race(check_path: Path, field_name: str = "path") -> None:
                if (
                    check_path == path
                    and field_name == "model path"
                    and not called["armed"]
                    and not check_path.exists()
                ):
                    original(check_path, field_name=field_name)
                    check_path.symlink_to(marker)
                    called["armed"] = True
                    return
                original(check_path, field_name=field_name)

            with mock.patch.object(models, "assert_no_symlink_ancestors", side_effect=assert_no_with_race):
                with self.assertRaisesRegex(models.ModelError, "model path must not pass through a symlink"):
                    models.download_model("test-target-race")

    def test_download_model_rejects_directory_target_symlink_before_final_replace(self) -> None:
        data = b"small model file"
        spec = models.ModelSpec(
            name="ct2-target-race",
            filename="ct2-target-race",
            size="2 KiB",
            sha1="",
            description="ct2 target symlink race test",
            backend="faster-whisper",
            model_format="ctranslate2",
            repo_id="example/ct2-target-race",
            files=("config.json",),
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "CATALOG", (spec,)),
            mock.patch("speed_of_cinnamon.models._open_model_download_url", return_value=FakeResponse(data)),
        ):
            path = models.model_path(spec)
            path.parent.mkdir(parents=True)
            marker = Path(tmp) / "ct2-target-race-marker"
            called = {"armed": False}
            original = models.assert_no_symlink_ancestors

            def assert_no_with_race(check_path: Path, field_name: str = "path") -> None:
                if check_path == path and field_name == "model path" and not called["armed"]:
                    original(check_path, field_name=field_name)
                    check_path.symlink_to(marker)
                    called["armed"] = True
                    return
                original(check_path, field_name=field_name)

            with mock.patch.object(models, "assert_no_symlink_ancestors", side_effect=assert_no_with_race):
                with self.assertRaisesRegex(models.ModelError, "model path must not pass through a symlink"):
                    models.download_model("ct2-target-race", force=True)

    def test_download_directory_model_uses_nofollow_parent_creation(self) -> None:
        data = b"small model file"
        spec = models.ModelSpec(
            name="ct2-parent-order",
            filename="ct2-parent-order",
            size="2 KiB",
            sha1="",
            description="ct2 parent safety order",
            backend="faster-whisper",
            model_format="ctranslate2",
            repo_id="example/ct2-parent-order",
            files=("config.json",),
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "CATALOG", (spec,)),
            mock.patch("speed_of_cinnamon.models._open_model_download_url", return_value=FakeResponse(data)),
        ):
            path = models.model_path(spec)
            events: list[str] = []

            original_ensure_directory = models.ensure_directory_without_following_symlinks
            original_assert_no_symlink_ancestors = models.assert_no_symlink_ancestors

            def record_ensure_directory(directory: Path, field_name: str = "path") -> int:
                if directory == path.parent:
                    events.append("ensure-parent")
                return original_ensure_directory(directory, field_name=field_name)

            def record_assert_no_symlink_ancestors(check_path: Path, field_name: str = "path") -> None:
                if field_name == "model path":
                    events.append(f"assert-path-{check_path}")
                return original_assert_no_symlink_ancestors(check_path, field_name=field_name)

            with (
                mock.patch.object(models, "ensure_directory_without_following_symlinks", side_effect=record_ensure_directory),
                mock.patch.object(models, "assert_no_symlink_ancestors", side_effect=record_assert_no_symlink_ancestors),
            ):
                models.download_model("ct2-parent-order", force=True)

        self.assertLess(
            events.index(f"assert-path-{path}"),
            events.index("ensure-parent"),
            "directory model path safety check should run before no-follow parent creation",
        )
        self.assertLess(
            events.index("ensure-parent"),
            len(events) - 1 - list(reversed(events)).index(f"assert-path-{path}"),
            "directory model path safety check should run after no-follow parent creation",
        )

    def test_download_model_downloads_multifile_ctranslate2_model(self) -> None:
        data = b"small model file"
        spec = models.ModelSpec(
            name="ct2-test",
            filename="ct2-test",
            size="2 KiB",
            sha1="",
            description="ct2 test",
            backend="faster-whisper",
            model_format="ctranslate2",
            repo_id="example/ct2-test",
            files=("config.json", "model.bin", "tokenizer.json"),
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "CATALOG", (spec,)),
            mock.patch("speed_of_cinnamon.models._open_model_download_url", side_effect=[
                FakeResponse(data),
                FakeResponse(data),
                FakeResponse(data),
            ]),
        ):
            payload = models.download_model("ct2-test")
            second_payload = models.download_model("ct2-test")
            path = models.model_path(spec)
            self.assertTrue(path.is_dir())
            self.assertTrue((path / "config.json").is_file())
            self.assertTrue((path / "model.bin").is_file())
            self.assertTrue((path / "tokenizer.json").is_file())
            self.assertEqual((path / "config.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual(payload["status"], "done")
        self.assertEqual(payload["downloaded"], True)
        self.assertTrue(payload["verified"])
        self.assertIn("already downloaded", second_payload["message"])

    def test_download_model_uses_nofollow_parent_creation(self) -> None:
        data = b"tiny model"
        spec = models.ModelSpec(
            name="test-parent-order",
            filename="ggml-test-parent-order.bin",
            size="1 KiB",
            sha1=hashlib.sha1(data).hexdigest(),
            description="test model parent safety order",
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "CATALOG", (spec,)),
            mock.patch("speed_of_cinnamon.models._open_model_download_url", return_value=FakeResponse(data)),
        ):
            path = models.model_path(spec)
            events: list[str] = []

            original_ensure_directory = models.ensure_directory_without_following_symlinks
            original_assert_no_symlink_ancestors = models.assert_no_symlink_ancestors

            def record_ensure_directory(directory: Path, field_name: str = "path") -> int:
                if directory == path.parent:
                    events.append("ensure-parent")
                return original_ensure_directory(directory, field_name=field_name)

            def record_assert_no_symlink_ancestors(check_path: Path, field_name: str = "path") -> None:
                if field_name == "model path":
                    events.append(f"assert-path-{check_path}")
                return original_assert_no_symlink_ancestors(check_path, field_name=field_name)

            with (
                mock.patch.object(models, "ensure_directory_without_following_symlinks", side_effect=record_ensure_directory),
                mock.patch.object(models, "assert_no_symlink_ancestors", side_effect=record_assert_no_symlink_ancestors),
            ):
                models.download_model("test-parent-order")

        self.assertLess(
            events.index(f"assert-path-{path}"),
            events.index("ensure-parent"),
            "model path safety check should run before no-follow parent creation",
        )
        self.assertLess(
            events.index("ensure-parent"),
            len(events) - 1 - list(reversed(events)).index(f"assert-path-{path}"),
            "model path safety check should run after no-follow parent creation",
        )

    def test_download_model_uses_fd_based_temporary_file_for_single_file_download(self) -> None:
        data = b"tiny model"
        spec = models.ModelSpec(
            name="test-single-tdir",
            filename="ggml-test-single-tdir.bin",
            size="1 KiB",
            sha1=hashlib.sha1(data).hexdigest(),
            description="test single-file temporary dir fd creation",
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "CATALOG", (spec,)),
            mock.patch("speed_of_cinnamon.models._open_model_download_url", return_value=FakeResponse(data)),
        ):
            open_parent_calls: list[int] = []
            temporary_file_calls: list[tuple[int, str]] = []

            original_open_parent = models._open_model_parent_directory
            original_temp_file = models._create_temporary_file_in_parent_directory

            def record_open_parent(path_arg: Path, root_arg: Path, field_name: str = "model path") -> int:
                parent_fd = original_open_parent(path_arg, root_arg, field_name=field_name)
                open_parent_calls.append(parent_fd)
                return parent_fd

            def record_temp_file(parent_fd: int, *, prefix: str) -> tuple[str, int]:
                temporary_file_calls.append((parent_fd, prefix))
                return original_temp_file(parent_fd, prefix=prefix)

            with (
                mock.patch.object(models, "_open_model_parent_directory", side_effect=record_open_parent),
                mock.patch.object(models, "_create_temporary_file_in_parent_directory", side_effect=record_temp_file),
            ):
                models.download_model("test-single-tdir")

        self.assertEqual(len(temporary_file_calls), 1, "single-file download should use secure temporary-file helper once")
        self.assertIn(temporary_file_calls[0][0], open_parent_calls)

    def test_download_model_removes_fd_temporary_file_when_response_too_large(self) -> None:
        spec = models.ModelSpec(
            name="test-single-tdir-too-large",
            filename="ggml-test-single-tdir-too-large.bin",
            size="1 KiB",
            sha1=hashlib.sha1(b"tiny model").hexdigest(),
            description="test failed single-file temporary cleanup",
        )
        oversized_content_length = models.MODEL_SIZE_SLACK_BYTES + 2048
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "CATALOG", (spec,)),
            mock.patch(
                "speed_of_cinnamon.models._open_model_download_url",
                return_value=FakeResponseWithLength(b"", oversized_content_length),
            ),
        ):
            path = models.model_path(spec)

            with self.assertRaisesRegex(models.ModelError, "downloaded model too large"):
                models.download_model("test-single-tdir-too-large")

            self.assertTrue(path.parent.exists())
            self.assertEqual([], list(path.parent.iterdir()))

    def test_download_directory_model_uses_fd_based_temporary_directory(self) -> None:
        data = b"small model file"
        spec = models.ModelSpec(
            name="ct2-directory-tdir",
            filename="ct2-directory-tdir",
            size="2 KiB",
            sha1="",
            description="test directory temporary dir fd creation",
            backend="faster-whisper",
            model_format="ctranslate2",
            repo_id="example/ct2-directory-tdir",
            files=("config.json",),
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "CATALOG", (spec,)),
            mock.patch("speed_of_cinnamon.models._open_model_download_url", return_value=FakeResponse(data)),
        ):
            open_parent_calls: list[int] = []
            temporary_directory_calls: list[tuple[int, str]] = []

            original_open_parent = models._open_model_parent_directory
            original_temp_directory = models._create_temporary_directory_in_parent_directory

            def record_open_parent(path_arg: Path, root_arg: Path, field_name: str = "model path") -> int:
                parent_fd = original_open_parent(path_arg, root_arg, field_name=field_name)
                open_parent_calls.append(parent_fd)
                return parent_fd

            def record_temp_directory(parent_fd: int, *, prefix: str) -> str:
                temporary_directory_calls.append((parent_fd, prefix))
                return original_temp_directory(parent_fd, prefix=prefix)

            with (
                mock.patch.object(models, "_open_model_parent_directory", side_effect=record_open_parent),
                mock.patch.object(models, "_create_temporary_directory_in_parent_directory", side_effect=record_temp_directory),
                mock.patch.object(models.tempfile, "mkdtemp", side_effect=AssertionError("mkdtemp should not be used for model temp directory")),
            ):
                models.download_model("ct2-directory-tdir")

        self.assertEqual(len(temporary_directory_calls), 1, "directory download should use secure temporary-directory helper once")
        self.assertGreaterEqual(len(open_parent_calls), 1, "directory download should open at least one model parent")
        self.assertIn(temporary_directory_calls[0][0], open_parent_calls)

    def test_multifile_model_symlink_path_is_not_downloaded(self) -> None:
        data = b"small model file"
        spec = models.ModelSpec(
            name="ct2-symlink-status",
            filename="ct2-symlink-status",
            size="2 KiB",
            sha1="",
            description="ct2 symlink status",
            backend="faster-whisper",
            model_format="ctranslate2",
            repo_id="example/ct2-symlink-status",
            files=("config.json",),
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "CATALOG", (spec,)),
        ):
            path = models.model_path(spec)
            target = Path(tmp) / "outside"
            target.mkdir()
            (target / "config.json").write_bytes(data)
            path.parent.mkdir(parents=True)
            path.symlink_to(target)

            with self.assertRaisesRegex(models.ModelError, "must not pass through a symlink"):
                models.model_status(spec, verify=True)

    def test_download_model_rejects_multifile_catalog_without_repo_id(self) -> None:
        spec = models.ModelSpec(
            name="ct2-bad",
            filename="ct2-bad",
            size="2 KiB",
            sha1="",
            description="ct2 bad",
            backend="faster-whisper",
            model_format="ctranslate2",
            files=("config.json", "model.bin"),
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "CATALOG", (spec,)),
        ):
            with self.assertRaisesRegex(models.ModelError, "missing repo_id"):
                models.download_model("ct2-bad")

    def test_download_url_rejects_non_huggingface_hosts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(models.ModelError, "host is not allowed"):
                models._download_url_to_file(
                    "https://example.com/model.bin",
                    Path(tmp),
                    1024,
                    "test",
                    prefix=".model.",
                )

    def test_download_url_rejects_redirects_to_unapproved_hosts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(models.ModelError, "host is not allowed"):
                with mock.patch(
                    "speed_of_cinnamon.models._open_model_download_url",
                    side_effect=fake_redirect(
                        "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.en.bin",
                        "https://evil.example/model.bin",
                    ),
                ):
                    models._download_url_to_file(
                        "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.en.bin",
                        Path(tmp),
                        1024,
                        "test",
                        prefix=".model.",
                    )

    def test_tiny_de_download_requires_exact_catalog_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(models.ModelError, "not allowed"):
                models._download_url_to_file(
                    "https://huggingface.co/other/model/resolve/main/ggml-tiny-de.bin",
                    Path(tmp),
                    1024,
                    "tiny-de",
                    prefix=".model.",
                )

    def test_download_url_allows_redirect_to_exact_allowed_url_with_query(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch(
                "speed_of_cinnamon.models._open_model_download_url",
                side_effect=[
                    fake_redirect(
                        models.TINY_DE_MODEL_URL,
                        "https://huggingface.co/wabisabisocial/whisper-tiny-german-ggml/resolve/main/ggml-tiny-de.bin?download=1",
                    ),
                    FakeResponseWithLength(b"model", 5),
                ],
            ):
                tmp_path, downloaded = models._download_url_to_file(
                    models.TINY_DE_MODEL_URL,
                    Path(tmp),
                    1024,
                    "tiny-de",
                    prefix=".model.",
                )
            self.assertEqual(downloaded, 5)
            self.assertTrue(tmp_path.exists())
            self.assertEqual(tmp_path.read_bytes(), b"model")

    def test_download_url_rejects_redirects_to_other_huggingface_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(models.ModelError, "redirect URL is not allowed"):
                with mock.patch(
                    "speed_of_cinnamon.models._open_model_download_url",
                    side_effect=fake_redirect(
                        models.TINY_DE_MODEL_URL,
                        "https://huggingface.co/other/repo/resolve/main/ggml-tiny-de.bin?download=1",
                    ),
                ):
                    models._download_url_to_file(
                        models.TINY_DE_MODEL_URL,
                        Path(tmp),
                        1024,
                        "tiny-de",
                        prefix=".model.",
                    )

    def test_download_model_raises_when_multifile_replace_fails(self) -> None:
        data = b"small model file"
        spec = models.ModelSpec(
            name="ct2-replace-fails",
            filename="ct2-replace-fails",
            size="2 KiB",
            sha1="",
            description="ct2 replace failure",
            backend="faster-whisper",
            model_format="ctranslate2",
            repo_id="example/ct2-replace-fails",
            files=("config.json",),
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "CATALOG", (spec,)),
            mock.patch("speed_of_cinnamon.models._open_model_download_url", return_value=FakeResponse(data)),
            mock.patch("speed_of_cinnamon.models.os.replace", side_effect=OSError("boom")),
        ):
            with self.assertRaisesRegex(models.ModelError, "failed to persist downloaded model file"):
                models.download_model("ct2-replace-fails")

    def test_download_model_restores_existing_multifile_model_when_final_replace_fails(self) -> None:
        data = b"small model file"
        spec = models.ModelSpec(
            name="ct2-final-replace-fails",
            filename="ct2-final-replace-fails",
            size="2 KiB",
            sha1="",
            description="ct2 final replace failure",
            backend="faster-whisper",
            model_format="ctranslate2",
            repo_id="example/ct2-final-replace-fails",
            files=("config.json",),
        )
        real_replace = os.replace

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "CATALOG", (spec,)),
            mock.patch("speed_of_cinnamon.models._open_model_download_url", return_value=FakeResponse(data)),
        ):
            path = models.model_path(spec)
            path.mkdir(parents=True)
            (path / "old.txt").write_text("old model", encoding="utf-8")

            def replace_or_fail(src: object, dst: object, *args: object, **kwargs: object) -> None:
                source = Path(src)
                if (
                    Path(dst).name == path.name
                    and source.name.startswith(f".{spec.filename}.")
                    and not source.name.endswith(".backup")
                ):
                    raise OSError("disk full")
                real_replace(src, dst, *args, **kwargs)

            with mock.patch("speed_of_cinnamon.models.os.replace", side_effect=replace_or_fail):
                with self.assertRaisesRegex(models.ModelError, "failed to persist downloaded model directory"):
                    models.download_model("ct2-final-replace-fails", force=True)

            self.assertEqual((path / "old.txt").read_text(encoding="utf-8"), "old model")
            self.assertEqual(list(path.parent.glob(f".{spec.filename}.*.backup")), [])

    def test_download_model_reports_multifile_backup_cleanup_failure_after_success(self) -> None:
        data = b"small model file"
        spec = models.ModelSpec(
            name="ct2-backup-cleanup-fails",
            filename="ct2-backup-cleanup-fails",
            size="2 KiB",
            sha1="",
            description="ct2 backup cleanup failure",
            backend="faster-whisper",
            model_format="ctranslate2",
            repo_id="example/ct2-backup-cleanup-fails",
            files=("config.json",),
        )
        real_rmtree = models.shutil.rmtree

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "CATALOG", (spec,)),
            mock.patch("speed_of_cinnamon.models._open_model_download_url", return_value=FakeResponse(data)),
        ):
            path = models.model_path(spec)
            path.mkdir(parents=True)
            (path / "old.txt").write_text("old model", encoding="utf-8")

            def rmtree_or_fail(target: object, *args: object, **kwargs: object) -> None:
                target_path = Path(target)
                if ".backup" in target_path.name:
                    raise OSError("cleanup failed")
                real_rmtree(target, *args, **kwargs)

            with mock.patch("speed_of_cinnamon.models.shutil.rmtree", side_effect=rmtree_or_fail):
                with self.assertRaisesRegex(models.ModelError, "failed to remove model backup"):
                    models.download_model("ct2-backup-cleanup-fails", force=True)

    def test_download_model_recovers_multifile_backup_cleanup_failure(self) -> None:
        data = b"small model file"
        spec = models.ModelSpec(
            name="ct2-backup-cleanup-recovered",
            filename="ct2-backup-cleanup-recovered",
            size="2 KiB",
            sha1="",
            description="ct2 backup cleanup recovery",
            backend="faster-whisper",
            model_format="ctranslate2",
            repo_id="example/ct2-backup-cleanup-recovered",
            files=("config.json",),
        )
        real_rmtree = models.shutil.rmtree

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "CATALOG", (spec,)),
            mock.patch("speed_of_cinnamon.models._open_model_download_url", return_value=FakeResponse(data)),
        ):
            path = models.model_path(spec)
            path.parent.mkdir(parents=True)

            def rmtree_or_fail(target: object, *args: object, **kwargs: object) -> None:
                target_path = Path(target)
                if target_path.name.endswith(".backup"):
                    raise OSError("cleanup failed")
                real_rmtree(target, *args, **kwargs)

            with mock.patch("speed_of_cinnamon.models.shutil.rmtree", side_effect=rmtree_or_fail):
                payload = models.download_model("ct2-backup-cleanup-recovered", force=True)

        self.assertEqual(payload["status"], "done")
        self.assertEqual(list(path.parent.glob(f".{spec.filename}.*.backup")), [])

    def test_download_model_force_replaces_file_with_multifile_model(self) -> None:
        data = b"small model file"
        spec = models.ModelSpec(
            name="ct2-replaces-file",
            filename="ct2-replaces-file",
            size="2 KiB",
            sha1="",
            description="ct2 replaces file",
            backend="faster-whisper",
            model_format="ctranslate2",
            repo_id="example/ct2-replaces-file",
            files=("config.json",),
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "CATALOG", (spec,)),
            mock.patch("speed_of_cinnamon.models._open_model_download_url", return_value=FakeResponse(data)),
        ):
            path = models.model_path(spec)
            path.parent.mkdir(parents=True)
            path.write_text("old file", encoding="utf-8")
            payload = models.download_model("ct2-replaces-file", force=True)

            self.assertEqual(payload["status"], "done")
            self.assertTrue(path.is_dir())
            self.assertEqual((path / "config.json").read_bytes(), data)
            self.assertEqual(list(path.parent.glob(f".{spec.filename}.*.backup")), [])

    def test_download_model_sets_private_permissions(self) -> None:
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
            mock.patch("speed_of_cinnamon.models._open_model_download_url", return_value=FakeResponse(data)),
        ):
            models.download_model("test")
            path = models.model_path(spec)
            mode = path.stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)

    def test_download_model_ignores_preexisting_tmp_symlink_leaf(self) -> None:
        data = b"tiny model"
        spec = models.ModelSpec(
            name="test-tmp-symlink",
            filename="ggml-test-tmp-symlink.bin",
            size="1 KiB",
            sha1=hashlib.sha1(data).hexdigest(),
            description="test model tmp symlink",
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "CATALOG", (spec,)),
            mock.patch("speed_of_cinnamon.models._open_model_download_url", return_value=FakeResponse(data)),
        ):
            path = models.model_path(spec)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_suffix(path.suffix + ".tmp")
            marker = Path(tmp) / "model-download-pwned-marker"
            tmp_path.symlink_to(marker)

            payload = models.download_model("test-tmp-symlink")

            self.assertEqual(payload["status"], "done")
            self.assertFalse(marker.exists(), "tmp symlink leaf should not have been followed")
            self.assertTrue(path.is_file())

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

    def test_remove_model_rejects_symlink_leaf_path(self) -> None:
        spec = models.ModelSpec(
            name="test-symlink-remove",
            filename="ggml-test-symlink-remove.bin",
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
            path.parent.mkdir(parents=True)
            target = Path(tmp) / "outside-model"
            target.write_bytes(b"payload")
            path.symlink_to(target.resolve())

            with self.assertRaisesRegex(models.ModelError, "must not pass through a symlink"):
                models.remove_model("test-symlink-remove")

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

    def test_default_model_path_skips_english_only_model_for_non_english_language(self) -> None:
        english_data = b"english model"
        multilingual_data = b"multilingual model"
        english = models.ModelSpec(
            name="tiny.en",
            filename="ggml-tiny.en.bin",
            size="1 KiB",
            sha1=hashlib.sha1(english_data).hexdigest(),
            description="english only",
            languages=("en",),
        )
        multilingual = models.ModelSpec(
            name="tiny",
            filename="ggml-tiny.bin",
            size="1 KiB",
            sha1=hashlib.sha1(multilingual_data).hexdigest(),
            description="multilingual",
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "CATALOG", (english, multilingual)),
        ):
            english_path = models.model_path(english)
            multilingual_path = models.model_path(multilingual)
            english_path.parent.mkdir(parents=True)
            english_path.write_bytes(english_data)
            multilingual_path.write_bytes(multilingual_data)

            self.assertEqual(models.default_whisper_cpp_model_path("en"), str(english_path))
            self.assertEqual(models.default_whisper_cpp_model_path("de"), str(multilingual_path))
            self.assertFalse(models.model_supports_language(english_path, "de"))
            self.assertTrue(models.model_supports_language(multilingual_path, "de"))

    def test_model_supports_language_rejects_null_byte_path(self) -> None:
        self.assertFalse(models.model_supports_language("a\x00.bin", "en"))

    def test_model_supports_language_rejects_escaped_null_path(self) -> None:
        self.assertFalse(models.model_supports_language("a\\x00.bin", "de"))

    def test_catalog_path_lookup_uses_filename_index_without_model_path_scan(self) -> None:
        spec = models.ModelSpec(
            name="indexed",
            filename="ggml-indexed.bin",
            size="1 KiB",
            sha1=hashlib.sha1(b"indexed").hexdigest(),
            description="indexed model",
        )
        with (
            mock.patch.object(models, "CATALOG", (spec,)),
            mock.patch("speed_of_cinnamon.models.model_path", side_effect=AssertionError("unexpected scan")),
        ):
            self.assertIs(models._catalog_model_for_path("/tmp/ggml-indexed.bin"), spec)
            self.assertEqual(models.model_backend_for_path("ggml-indexed.bin"), spec.backend)

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
                "speed_of_cinnamon.models._open_model_download_url",
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
                "speed_of_cinnamon.models._open_model_download_url",
                return_value=FakeResponseWithLength(data=data, content_length=10),
            ),
        ):
            with self.assertRaisesRegex(models.ModelError, "size mismatch"):
                models.download_model("mismatch")

    def test_parse_model_size_bytes_rejects_unknown_unit(self) -> None:
        with self.assertRaisesRegex(models.ModelError, "unsupported format"):
            models._parse_model_size_bytes("1 XB")

    def test_parse_model_size_bytes_rejects_non_positive_sizes(self) -> None:
        with self.assertRaisesRegex(models.ModelError, "must be positive"):
            models._parse_model_size_bytes("0 MiB")
        with self.assertRaisesRegex(models.ModelError, "must be positive"):
            models._parse_model_size_bytes("-3 MiB")

    def test_parse_model_size_bytes_rejects_empty_value(self) -> None:
        with self.assertRaisesRegex(models.ModelError, "empty value"):
            models._parse_model_size_bytes("   ")

    def test_parse_model_size_bytes_rejects_non_numeric_value(self) -> None:
        with self.assertRaisesRegex(models.ModelError, "invalid model size"):
            models._parse_model_size_bytes("abc MiB")

    def test_parse_model_size_bytes_rejects_non_text(self) -> None:
        with self.assertRaisesRegex(models.ModelError, "must be text"):
            models._parse_model_size_bytes(42)  # type: ignore[arg-type]

    def test_download_model_rejects_unknown_model_size_format(self) -> None:
        spec = models.ModelSpec(
            name="bad-size",
            filename="ggml-bad-size.bin",
            size="1",  # missing unit
            sha1=hashlib.sha1(b"x").hexdigest(),
            description="bad size format",
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "CATALOG", (spec,)),
        ):
            with self.assertRaisesRegex(models.ModelError, "unsupported format"):
                models.download_model("bad-size")

    def test_model_path_rejects_absolute_catalog_filename(self) -> None:
        spec = models.ModelSpec(
            name="absolute",
            filename="/tmp/ggml-absolute.bin",
            size="1 KiB",
            sha1=hashlib.sha1(b"absolute").hexdigest(),
            description="absolute path",
        )
        with self.assertRaisesRegex(models.ModelError, "model filename must be a relative path without parent traversal"):
            models.model_path(spec)

    def test_download_model_rejects_multifile_catalog_path_traversal(self) -> None:
        spec = models.ModelSpec(
            name="ct2-escape",
            filename="ct2-escape",
            size="2 KiB",
            sha1="",
            description="ct2 escape",
            backend="faster-whisper",
            model_format="ctranslate2",
            repo_id="example/ct2-escape",
            files=("config.json", "../model.bin"),
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "CATALOG", (spec,)),
        ):
            with self.assertRaisesRegex(models.ModelError, "model file path must be a relative path without parent traversal"):
                models.download_model("ct2-escape")

    def test_resolve_model_rejects_non_text_name(self) -> None:
        with self.assertRaisesRegex(models.ModelError, "model name must be text"):
            models.resolve_model(1)  # type: ignore[arg-type]

    def test_model_supports_language_rejects_non_text_inputs(self) -> None:
        self.assertFalse(models.model_supports_language(12, "en"))  # type: ignore[arg-type]
        self.assertFalse(models.model_supports_language("models/ggml-tiny.bin", 12))

    def test_model_path_is_english_only_rejects_non_text_path(self) -> None:
        self.assertFalse(models.model_path_is_english_only(12))  # type: ignore[arg-type]

    def test_contains_escaped_null_rejects_non_text(self) -> None:
        with self.assertRaisesRegex(models.ModelError, "must be text"):
            models._contains_escaped_null(12)  # type: ignore[arg-type]

    def test_parse_content_length_rejects_invalid_or_non_positive_headers(self) -> None:
        with self.assertRaisesRegex(models.ModelError, "invalid content-length header"):
            models._parse_content_length("x")
        with self.assertRaisesRegex(models.ModelError, "invalid content-length header"):
            models._parse_content_length("0")
        with self.assertRaisesRegex(models.ModelError, "invalid content-length header"):
            models._parse_content_length("-10")
        with self.assertRaisesRegex(models.ModelError, "invalid content-length header"):
            models._parse_content_length(b"10")
        with self.assertRaisesRegex(models.ModelError, "invalid content-length header"):
            models._parse_content_length(True)  # type: ignore[arg-type]
        with self.assertRaisesRegex(models.ModelError, "invalid content-length header"):
            models._parse_content_length(False)  # type: ignore[arg-type]

    def test_download_model_rejects_invalid_content_length_header(self) -> None:
        data = b"model"
        expected_checksum = hashlib.sha1(data).hexdigest()
        spec = models.ModelSpec(
            name="bad-length",
            filename="ggml-bad-length.bin",
            size="1 MiB",
            sha1=expected_checksum,
            description="invalid content length",
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "CATALOG", (spec,)),
            mock.patch(
                "speed_of_cinnamon.models._open_model_download_url",
                return_value=FakeResponseWithLength(data=data, content_length=-1),
            ),
        ):
            with self.assertRaisesRegex(models.ModelError, "invalid content-length header"):
                models.download_model("bad-length")

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
            mock.patch("speed_of_cinnamon.models._open_model_download_url", return_value=FakeResponse(data)),
        ):
            with self.assertRaisesRegex(models.ModelError, "failed to persist downloaded model file"):
                models.download_model("test")
        path = models.model_path(spec)
        self.assertFalse(path.exists())
        self.assertFalse(path.with_suffix(path.suffix + ".tmp").exists())

    def test_download_model_preserves_existing_checksum_cache_when_atomic_replace_fails(self) -> None:
        old_data = b"old model"
        new_data = b"new model"
        old_checksum = hashlib.sha1(old_data).hexdigest()
        spec = models.ModelSpec(
            name="replace-fails-with-cache",
            filename="ggml-replace-fails-with-cache.bin",
            size="1 KiB",
            sha1=hashlib.sha1(new_data).hexdigest(),
            description="replace failure with cache",
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "CATALOG", (spec,)),
            mock.patch.object(models, "_model_checksum_cache", {}),
            mock.patch.object(models, "_model_checksum_cache_loaded", True),
            mock.patch("speed_of_cinnamon.models._open_model_download_url", return_value=FakeResponse(new_data)),
        ):
            path = models.model_path(spec)
            path.parent.mkdir(parents=True)
            path.write_bytes(old_data)
            models._set_model_checksum_cache(path, old_checksum, path.stat())

            with mock.patch("speed_of_cinnamon.models.os.replace", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(models.ModelError, "failed to persist downloaded model file"):
                    models.download_model("replace-fails-with-cache", force=True)

            self.assertEqual(path.read_bytes(), old_data)
            self.assertEqual(models._model_checksum_cache[str(path)]["checksum"], old_checksum)

    def test_download_model_restores_existing_file_when_cache_update_fails_after_replace(self) -> None:
        old_data = b"old model"
        new_data = b"new model"
        old_checksum = hashlib.sha1(old_data).hexdigest()
        spec = models.ModelSpec(
            name="cache-update-fails-after-replace",
            filename="ggml-cache-update-fails-after-replace.bin",
            size="1 KiB",
            sha1=hashlib.sha1(new_data).hexdigest(),
            description="cache update failure after replace",
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "CATALOG", (spec,)),
            mock.patch.object(models, "_model_checksum_cache", {}),
            mock.patch.object(models, "_model_checksum_cache_loaded", True),
            mock.patch("speed_of_cinnamon.models._open_model_download_url", return_value=FakeResponse(new_data)),
        ):
            path = models.model_path(spec)
            path.parent.mkdir(parents=True)
            path.write_bytes(old_data)
            models._set_model_checksum_cache(path, old_checksum, path.stat())

            with mock.patch("speed_of_cinnamon.models._set_model_checksum_cache", side_effect=models.ModelError("cache fail")):
                with self.assertRaisesRegex(models.ModelError, "cache fail"):
                    models.download_model("cache-update-fails-after-replace", force=True)

            self.assertEqual(path.read_bytes(), old_data)
            self.assertEqual(models._model_checksum_cache[str(path)]["checksum"], old_checksum)

    def test_download_model_removes_new_file_when_cache_update_fails_without_backup(self) -> None:
        new_data = b"new model"
        spec = models.ModelSpec(
            name="cache-update-fails-without-backup",
            filename="ggml-cache-update-fails-without-backup.bin",
            size="1 KiB",
            sha1=hashlib.sha1(new_data).hexdigest(),
            description="cache update failure without backup",
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "CATALOG", (spec,)),
            mock.patch.object(models, "_model_checksum_cache", {}),
            mock.patch.object(models, "_model_checksum_cache_loaded", True),
            mock.patch("speed_of_cinnamon.models._open_model_download_url", return_value=FakeResponse(new_data)),
        ):
            path = models.model_path(spec)
            with mock.patch("speed_of_cinnamon.models._set_model_checksum_cache", side_effect=models.ModelError("cache fail")):
                with self.assertRaisesRegex(models.ModelError, "cache fail"):
                    models.download_model("cache-update-fails-without-backup", force=True)

            self.assertFalse(path.exists())

    def test_download_model_reports_backup_cleanup_failure_after_success(self) -> None:
        old_data = b"old model"
        new_data = b"new model"
        spec = models.ModelSpec(
            name="backup-cleanup-fails",
            filename="ggml-backup-cleanup-fails.bin",
            size="1 KiB",
            sha1=hashlib.sha1(new_data).hexdigest(),
            description="backup cleanup failure",
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "CATALOG", (spec,)),
            mock.patch("speed_of_cinnamon.models._open_model_download_url", return_value=FakeResponse(new_data)),
        ):
            path = models.model_path(spec)
            path.parent.mkdir(parents=True)
            path.write_bytes(old_data)
            with mock.patch("speed_of_cinnamon.models.Path.unlink", side_effect=OSError("cleanup failed")):
                with self.assertRaisesRegex(models.ModelError, "failed to remove model backup"):
                    models.download_model("backup-cleanup-fails", force=True)

    def test_download_model_force_replaces_directory_with_file_model(self) -> None:
        new_data = b"new model"
        spec = models.ModelSpec(
            name="file-replaces-directory",
            filename="ggml-file-replaces-directory.bin",
            size="1 KiB",
            sha1=hashlib.sha1(new_data).hexdigest(),
            description="file replaces directory",
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "CATALOG", (spec,)),
            mock.patch("speed_of_cinnamon.models._open_model_download_url", return_value=FakeResponse(new_data)),
        ):
            path = models.model_path(spec)
            path.mkdir(parents=True)
            (path / "old.txt").write_text("old model", encoding="utf-8")
            payload = models.download_model("file-replaces-directory", force=True)

            self.assertEqual(payload["status"], "done")
            self.assertTrue(path.is_file())
            self.assertEqual(path.read_bytes(), new_data)
            self.assertEqual(list(path.parent.glob(f".{path.name}.*.backup")), [])

    def test_restore_model_file_backup_reports_restore_failure(self) -> None:
        old_data = b"old model"
        new_data = b"new model"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "model.bin"
            backup = Path(tmp) / ".model.bin.backup"
            path.write_bytes(old_data)
            backup.write_bytes(new_data)

            with mock.patch("speed_of_cinnamon.models.os.replace", side_effect=OSError("restore failed")):
                with self.assertRaises(OSError):
                    models._restore_model_file_backup(path, backup)
            self.assertEqual(path.read_bytes(), old_data)
            self.assertEqual(backup.read_bytes(), new_data)

    def test_replace_model_sibling_path_rejects_symlink_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "real"
            real.mkdir()
            link = root / "link"
            link.symlink_to(real, target_is_directory=True)
            source = link / "source.bin"
            target = link / "target.bin"
            (real / "source.bin").write_bytes(b"model")

            with self.assertRaisesRegex(models.ModelError, "must not pass through a symlink"):
                models._replace_model_sibling_path(source, target, root, field_name="model path")

            self.assertFalse((real / "target.bin").exists())

    def test_model_status_rejects_non_boolean_verify(self) -> None:
        with self.assertRaisesRegex(models.ModelError, "verify must be a boolean"):
            models.model_status(models.CATALOG[0], verify="true")  # type: ignore[arg-type]

    def test_download_model_rejects_non_boolean_force(self) -> None:
        with self.assertRaisesRegex(models.ModelError, "force must be a boolean"):
            models.download_model("tiny.en", force="yes")  # type: ignore[arg-type]

    def test_assert_download_url_rejects_control_character(self) -> None:
        with self.assertRaisesRegex(models.ModelError, "contains invalid control character"):
            models._assert_download_url("https://huggingface.co/example/model\n.bin")

    def test_assert_download_url_rejects_escaped_control_character(self) -> None:
        with self.assertRaisesRegex(models.ModelError, "contains invalid control character"):
            models._assert_download_url("https://huggingface.co/example/model\\n.bin")

    def test_assert_download_url_rejects_unapproved_host_when_allowlisted(self) -> None:
        with self.assertRaisesRegex(models.ModelError, "host is not allowed"):
            models._assert_download_url("https://example.com/model.bin", allowed_hosts={"huggingface.co"})

    def test_download_model_rejects_catalog_url_outside_huggingface(self) -> None:
        spec = models.ModelSpec(
            name="evil-host",
            filename="ggml-evil-host.bin",
            size="1 KiB",
            sha1="not-used",
            description="evil host",
            download_url="https://example.com/ggml-evil-host.bin",
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
            mock.patch.object(models, "CATALOG", (spec,)),
        ):
            with self.assertRaisesRegex(models.ModelError, "host is not allowed"):
                models.download_model("evil-host")


if __name__ == "__main__":
    unittest.main()
