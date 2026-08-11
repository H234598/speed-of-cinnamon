from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_RPM = REPO_ROOT / "scripts" / "build-rpm.sh"


class BuildRpmStaticTest(unittest.TestCase):
    def test_build_rpm_cleanup_requires_expected_identity(self) -> None:
        source = BUILD_RPM.read_text(encoding="utf-8")

        self.assertIn(
            'rpmbuild_tmpdir_identity="$("${safe_fs_cmd[@]}" identity build-rpm "${rpmbuild_tmpdir}" --kind dir)"',
            source,
        )
        self.assertIn(
            'stage_topdir_identity="$("${safe_fs_cmd[@]}" identity build-rpm "${stage_topdir}" --kind dir)"',
            source,
        )
        self.assertIn('--expected-identity "${rpmbuild_tmpdir_identity}"', source)
        self.assertIn('--expected-identity "${stage_topdir_identity}"', source)


if __name__ == "__main__":
    unittest.main()
