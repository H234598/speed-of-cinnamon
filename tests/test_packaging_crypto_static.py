from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class PackagingCryptoStaticTest(unittest.TestCase):
    def test_rpm_specs_enforce_project_crypto_floor(self) -> None:
        for name in ("speed-of-cinnamon.spec", "speed-of-cinnamon-generic.spec"):
            source = (REPO_ROOT / "packaging" / name).read_text(encoding="utf-8")
            self.assertIn("Requires:       python3-cryptography >= 50.0.0", source)

    def test_snap_uses_hash_locked_project_runtime_dependency(self) -> None:
        manifest = (REPO_ROOT / "snap" / "snapcraft.yaml").read_text(encoding="utf-8")
        requirements = (REPO_ROOT / "snap" / "requirements.txt").read_text(encoding="utf-8")
        ci_lock = (REPO_ROOT / ".github" / "requirements" / "ci-project.txt").read_text(encoding="utf-8")

        self.assertNotIn("- python3-cryptography", manifest)
        self.assertIn("architectures:\n  - amd64", manifest)
        self.assertIn("- python3-pip", manifest)
        self.assertIn("--require-hashes", manifest)
        self.assertIn("-r \"${CRAFT_PROJECT_DIR}/snap/requirements.txt\"", manifest)
        self.assertIn("--require-hashes", requirements)
        self.assertIn("-r ../.github/requirements/ci-project.txt", requirements)
        self.assertIn("cryptography==50.0.0", ci_lock)


if __name__ == "__main__":
    unittest.main()
