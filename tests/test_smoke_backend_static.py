from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SMOKE_BACKEND = REPO_ROOT / "scripts" / "smoke-backend.sh"


class SmokeBackendStaticTest(unittest.TestCase):
    def test_smoke_backend_validates_tmpdir_before_mktemp(self) -> None:
        source = SMOKE_BACKEND.read_text(encoding="utf-8")

        self.assertIn("resolve_smoke_tmp_root() {", source)
        self.assertIn('temporary root must be an absolute path: %s\\n', source)
        self.assertIn('temporary root must not be a symlink: %s\\n', source)
        self.assertIn('temporary root is not a writable directory: %s\\n', source)
        self.assertIn('safe_fs_cmd=(python3 "${safe_fs}")', source)
        self.assertIn('smoke_tmp_root="$(resolve_smoke_tmp_root)"', source)
        self.assertIn('smoke_root="$(mktemp -d "${smoke_tmp_root}/speed-of-cinnamon-smoke-XXXXXX")"', source)
        self.assertIn('smoke_root_identity=""', source)
        self.assertIn('smoke_root_abs="$(realpath "${smoke_root}")', source)
        self.assertIn('temporary smoke directory escaped temporary root', source)
        self.assertIn('"${safe_fs_cmd[@]}" remove smoke-backend "${smoke_root}" --kind dir', source)
        self.assertIn('--expected-identity "${smoke_root_identity}"', source)
        self.assertIn('refusing smoke cleanup without verified identity', source)
        self.assertNotIn('mktemp -d "${TMPDIR:-/tmp}/speed-of-cinnamon-smoke-XXXXXX"', source)
        self.assertNotIn('rm -rf -- "${smoke_root}"', source)


if __name__ == "__main__":
    unittest.main()
