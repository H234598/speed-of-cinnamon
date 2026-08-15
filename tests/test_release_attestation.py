from __future__ import annotations

import json
import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from speed_of_cinnamon.models import CATALOG, source_attestation_snapshot


REPO_ROOT = Path(__file__).resolve().parents[1]
RELEASE_ROOT = REPO_ROOT / "release-attestations"
VERIFY = REPO_ROOT / "scripts" / "verify-release-attestation.py"
EXPORT = REPO_ROOT / "scripts" / "export-release-attestations.sh"


class ReleaseAttestationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if RELEASE_ROOT.is_symlink():
            raise AssertionError("release-attestations must not be a symlink")
        cls.created_root = not RELEASE_ROOT.exists()
        RELEASE_ROOT.mkdir(mode=0o755, exist_ok=True)

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.created_root:
            RELEASE_ROOT.rmdir()

    def setUp(self) -> None:
        self.bundle = Path(tempfile.mkdtemp(prefix=".test-", dir=RELEASE_ROOT))

    def tearDown(self) -> None:
        if self.bundle.is_symlink():
            self.bundle.unlink()
        elif self.bundle.exists():
            shutil.rmtree(self.bundle)

    def _model_entry(self, name: str) -> dict[str, object]:
        spec = next(model for model in CATALOG if model.name == name)
        hashes = dict(spec.file_sha1s) if spec.file_sha1s else {spec.filename: spec.sha1}
        return {
            "backend": spec.backend,
            "files": [{"name": filename, "sha1": digest} for filename, digest in hashes.items()],
            "languages": list(spec.languages),
            "model_format": spec.model_format,
            "name": spec.name,
            "tested_languages": list(spec.languages or ("de", "en")),
        }

    def _write_bundle(self, *, head: str) -> None:
        source = source_attestation_snapshot(REPO_ROOT)
        created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        expires_at = (datetime.fromisoformat(created_at.replace("Z", "+00:00")) + timedelta(hours=24)).isoformat().replace(
            "+00:00", "Z"
        )
        (self.bundle / "real-e2e-attestation.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "created_at": created_at,
                    "expires_at": expires_at,
                    "git_head": head,
                    "matrix": ["live-applet", "arecord", "pipewire", "openai-compatible", "flex-on", "flex-off"],
                    "source": source,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (self.bundle / "local-model-e2e-attestation.json").write_text(
            json.dumps(
                {
                    "case_count": 2,
                    "created_at": created_at,
                    "expires_at": expires_at,
                    "ct2_case_count": 1,
                    "ggml_case_count": 1,
                    "git_head": head,
                    "schema_version": 1,
                    "matrix": [
                        "local-models",
                        "generated-audio",
                        "ggml",
                        "ctranslate2",
                        "explicit-backend",
                        "auto-backend",
                        "no-microphone",
                        "no-clipboard",
                    ],
                    "models": [self._model_entry("base"), self._model_entry("ct2-base")],
                    "source": source,
                }
            )
            + "\n",
            encoding="utf-8",
        )

    def _run(self, head: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(VERIFY), str(self.bundle), str(REPO_ROOT), head],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def _load_verifier(self):
        spec = importlib.util.spec_from_file_location("verify_release_attestation_test", VERIFY)
        self.assertIsNotNone(spec)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_verifier_reports_descriptor_cleanup_failure(self) -> None:
        module = self._load_verifier()
        path = self.bundle / "real-e2e-attestation.json"
        path.write_text("{}\n", encoding="utf-8")
        with mock.patch.object(module.os, "close", side_effect=OSError("close failed")):
            with self.assertRaisesRegex(module.AttestationError, "descriptor cleanup failed"):
                module._read_json(path)

    def test_verifier_preserves_primary_error_when_descriptor_cleanup_fails(self) -> None:
        module = self._load_verifier()
        path = self.bundle / "real-e2e-attestation.json"
        path.write_text("{}\n", encoding="utf-8")
        with (
            mock.patch.object(module.os, "fstat", side_effect=OSError("fstat failed")),
            mock.patch.object(module.os, "close", side_effect=OSError("close failed")),
        ):
            with self.assertRaisesRegex(module.AttestationError, "cannot open release attestation") as caught:
                module._read_json(path)
        self.assertIn("release attestation descriptor cleanup failed", getattr(caught.exception, "__notes__", ()))

    def test_source_and_model_bound_bundle_is_accepted(self) -> None:
        head = "a" * 40
        self._write_bundle(head=head)
        result = self._run(head)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_bundle_rejects_source_snapshot_change(self) -> None:
        head = "b" * 40
        self._write_bundle(head=head)
        path = self.bundle / "real-e2e-attestation.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["source"][0]["sha256"] = "0" * 64
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        result = self._run(head)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("source snapshot", result.stderr)

    def test_bundle_rejects_wrong_tested_commit(self) -> None:
        self._write_bundle(head="c" * 40)
        result = self._run("d" * 40)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("wrong tested commit", result.stderr)

    def test_bundle_rejects_unsupported_schema_version(self) -> None:
        head = "1" * 40
        self._write_bundle(head=head)
        path = self.bundle / "real-e2e-attestation.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["schema_version"] = 1.0
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        result = self._run(head)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("schema version", result.stderr)

    def test_bundle_rejects_invalid_expiry_contract(self) -> None:
        head = "2" * 40
        self._write_bundle(head=head)
        path = self.bundle / "real-e2e-attestation.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["expires_at"] = payload["created_at"]
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        result = self._run(head)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("expiry contract", result.stderr)

    def test_bundle_rejects_unexpected_top_level_field(self) -> None:
        head = "3" * 40
        self._write_bundle(head=head)
        path = self.bundle / "real-e2e-attestation.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["private_transcript"] = "must not enter release evidence"
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        result = self._run(head)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unexpected fields", result.stderr)

    def test_bundle_rejects_duplicate_model_file_entries(self) -> None:
        head = "7" * 40
        self._write_bundle(head=head)
        path = self.bundle / "local-model-e2e-attestation.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        first_file = payload["models"][0]["files"][0]
        payload["models"][0]["files"].append(dict(first_file))
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        result = self._run(head)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("model files are invalid", result.stderr)

    def test_bundle_rejects_duplicate_matrix_entries(self) -> None:
        head = "8" * 40
        self._write_bundle(head=head)
        path = self.bundle / "real-e2e-attestation.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["matrix"].append("flex-on")
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        result = self._run(head)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("matrix is incomplete", result.stderr)

    def test_bundle_rejects_symlinked_bundle_directory(self) -> None:
        head = "e" * 40
        self._write_bundle(head=head)
        target = self.bundle.with_name(self.bundle.name + "-target")
        target.mkdir(mode=0o700)
        try:
            for path in self.bundle.iterdir():
                path.rename(target / path.name)
            shutil.rmtree(self.bundle)
            self.bundle.symlink_to(target, target_is_directory=True)
            result = self._run(head)
        finally:
            if self.bundle.is_symlink():
                self.bundle.unlink()
            shutil.rmtree(target)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("bundle directory is unsafe", result.stderr)

    def test_bundle_rejects_symlinked_release_root(self) -> None:
        head = "f" * 40
        self._write_bundle(head=head)
        moved_root = Path(tempfile.mkdtemp(prefix=".release-root-", dir=REPO_ROOT.parent))
        moved_root.rmdir()
        RELEASE_ROOT.rename(moved_root)
        RELEASE_ROOT.symlink_to(moved_root, target_is_directory=True)
        try:
            result = self._run(head)
        finally:
            RELEASE_ROOT.unlink()
            moved_root.rename(RELEASE_ROOT)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("root directory is unsafe", result.stderr)

    def test_exporter_rejects_unsafe_release_tags(self) -> None:
        for tag in ("../v1.2.3", "v1.2", "v1.2.3/../../outside"):
            result = subprocess.run(
                [str(EXPORT), tag],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2, tag)
            self.assertIn("usage:", result.stderr, tag)


if __name__ == "__main__":
    unittest.main()
