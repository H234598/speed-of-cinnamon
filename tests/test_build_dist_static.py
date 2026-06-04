from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_DIST = REPO_ROOT / "scripts" / "build-dist.sh"


class BuildDistStaticTest(unittest.TestCase):
    def test_build_dist_uses_safe_fs_for_source_copying(self) -> None:
        source = BUILD_DIST.read_text(encoding="utf-8")

        self.assertIn('safe_fs="${repo_dir}/scripts/safe-local-fs.py"', source)
        self.assertIn('python3 "${safe_fs}" install-tree build-dist "${source_path}" "${target_path}"', source)
        self.assertIn('python3 "${safe_fs}" copy-file build-dist "${source_path}" "${target_path}" 0644', source)
        self.assertNotIn('cp -a "${repo_dir}/${path}" "${work_dir}/${package}/"', source)


if __name__ == "__main__":
    unittest.main()
