from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest import mock

from speed_of_cinnamon import models


class ModelAttestationTests(unittest.TestCase):
    def test_snapshot_contains_sorted_file_fingerprints_without_absolute_paths(self) -> None:
        spec = models.ModelSpec(
            name="snapshot-model",
            filename="snapshot-model",
            size="1 MiB",
            sha1="",
            description="test",
            backend="faster-whisper",
            model_format="ctranslate2",
            files=("z.bin", "a.json"),
        )
        with (
            mock.patch.object(models, "CATALOG", (spec,)),
            mock.patch.object(models, "model_path", return_value=Path("/tmp/snapshot-model")),
            mock.patch.object(models, "_model_is_downloaded", return_value=True),
            mock.patch.object(models, "_model_is_verified", return_value=True),
            mock.patch.object(
                models,
                "_cached_verified_sha1",
                side_effect=lambda path: {"a.json": "a" * 40, "z.bin": "b" * 40}[path.name],
            ),
        ):
            snapshot = models.model_attestation_snapshot()
        self.assertEqual(snapshot[0]["name"], "snapshot-model")
        self.assertEqual(
            snapshot[0]["files"],
            [{"name": "a.json", "sha1": "a" * 40}, {"name": "z.bin", "sha1": "b" * 40}],
        )
        self.assertNotIn("path", snapshot[0])

    def test_snapshot_rejects_downloaded_unverified_model(self) -> None:
        spec = models.ModelSpec(
            name="unverified-model",
            filename="unverified.bin",
            size="1 MiB",
            sha1="a" * 40,
            description="test",
        )
        with (
            mock.patch.object(models, "CATALOG", (spec,)),
            mock.patch.object(models, "model_path", return_value=Path("/tmp/unverified.bin")),
            mock.patch.object(models, "_model_is_downloaded", return_value=True),
            mock.patch.object(models, "_model_is_verified", return_value=False),
        ):
            with self.assertRaisesRegex(models.ModelError, "model integrity is not verified"):
                models.model_attestation_snapshot()

    def test_all_ctranslate2_catalog_entries_have_pinned_complete_files(self) -> None:
        ctranslate2 = [model for model in models.CATALOG if model.model_format == "ctranslate2"]
        self.assertTrue(ctranslate2)
        for model in ctranslate2:
            self.assertEqual(
                {filename for filename, _checksum in model.file_sha1s},
                set(model.files),
                model.name,
            )
            self.assertTrue(all(len(checksum) == 40 for _filename, checksum in model.file_sha1s), model.name)

    def test_base_int8_tokenizer_uses_explicit_pinned_companion_repository(self) -> None:
        model = models.resolve_model("ct2-base-int8")
        urls = dict(models.model_download_urls(model))
        self.assertEqual(
            urls["tokenizer.json"],
            "https://huggingface.co/Systran/faster-whisper-base/resolve/main/tokenizer.json",
        )

    def test_small_de_tokenizer_uses_explicit_pinned_companion_repository(self) -> None:
        model = models.resolve_model("ct2-small-de")
        urls = dict(models.model_download_urls(model))
        self.assertEqual(
            urls["tokenizer.json"],
            "https://huggingface.co/Systran/faster-whisper-small/resolve/main/tokenizer.json",
        )

    def test_current_huggingface_xet_storage_host_is_path_and_filename_bound(self) -> None:
        allowed = "https://huggingface.co/Systran/faster-whisper-base/resolve/main/model.bin"
        redirect = (
            "https://us.aws.cdn.hf.co/xet-bridge-us/abc/model.bin"
            "?response-content-disposition=inline%3B+filename%3D%22model.bin%22"
        )
        self.assertTrue(models._download_redirect_matches_allowed_url(redirect, allowed))
        hash_redirect = (
            "https://us.aws.cdn.hf.co/xet-bridge-us/abc/"
            "37fe974d1e5183fbce69f9a366092e4ddd9fd1fc1919cea6b9a282431e992fac"
            "?response-content-disposition=inline%3B+filename%3D%22model.bin%22"
        )
        self.assertTrue(models._download_redirect_matches_allowed_url(hash_redirect, allowed))

    def test_source_attestation_snapshot_is_path_free_and_rejects_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "scripts").mkdir()
            (root / "files").mkdir()
            (root / "snap").mkdir()
            (root / "Makefile").write_text("check\n", encoding="utf-8")
            (root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
            (root / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
            (root / "scripts" / "run.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (root / "files" / "metadata.json").write_text("{}\n", encoding="utf-8")
            (root / "snap" / "snapcraft.yaml").write_text("name: test\n", encoding="utf-8")
            snapshot = models.source_attestation_snapshot(root)
            self.assertTrue(snapshot)
            self.assertTrue(all(set(entry) == {"path", "sha256"} for entry in snapshot))
            self.assertTrue(all(not Path(entry["path"]).is_absolute() for entry in snapshot))
            with mock.patch.object(models.os, "O_NOFOLLOW", None, create=True):
                with self.assertRaisesRegex(models.ModelError, "secure no-follow"):
                    models.source_attestation_snapshot(root)
            (root / "scripts" / "unsafe").symlink_to(root / "src" / "main.py")
            with self.assertRaisesRegex(models.ModelError, "not a regular file"):
                models.source_attestation_snapshot(root)

    def test_source_attestation_reports_descriptor_cleanup_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "scripts").mkdir()
            (root / "files").mkdir()
            (root / "snap").mkdir()
            (root / "Makefile").write_text("check\n", encoding="utf-8")
            (root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
            (root / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
            with mock.patch.object(models.os, "close", side_effect=OSError("close failed")):
                with self.assertRaisesRegex(models.ModelError, "descriptor cleanup failed"):
                    models.source_attestation_snapshot(root)

    def test_source_attestation_preserves_read_error_when_descriptor_cleanup_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "scripts").mkdir()
            (root / "files").mkdir()
            (root / "snap").mkdir()
            (root / "Makefile").write_text("check\n", encoding="utf-8")
            (root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
            (root / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
            with (
                mock.patch.object(models.os, "fstat", side_effect=OSError("fstat failed")),
                mock.patch.object(models.os, "close", side_effect=OSError("close failed")),
            ):
                with self.assertRaisesRegex(models.ModelError, "source attestation file cannot be read") as caught:
                    models.source_attestation_snapshot(root)
            self.assertIn("source attestation descriptor cleanup failed", getattr(caught.exception, "__notes__", ()))


if __name__ == "__main__":
    unittest.main()
