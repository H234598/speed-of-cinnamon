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

    def test_project_and_spec_reads_are_bounded(self) -> None:
        source = BUILD_RPM.read_text(encoding="utf-8")

        self.assertIn("MAX_PROJECT_METADATA_BYTES = 1 << 20", source)
        self.assertIn("MAX_RPM_SPEC_BYTES = 1 << 20", source)
        self.assertIn("handle.read(MAX_PROJECT_METADATA_BYTES + 1)", source)
        self.assertIn("handle.read(MAX_RPM_SPEC_BYTES + 1)", source)


if __name__ == "__main__":
    unittest.main()
