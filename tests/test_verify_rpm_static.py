from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
VERIFY_RPM = REPO_ROOT / "scripts" / "verify-rpm.sh"


class VerifyRpmStaticTest(unittest.TestCase):
    def test_verification_cleanup_requires_expected_identity(self) -> None:
        source = VERIFY_RPM.read_text(encoding="utf-8")

        self.assertIn(
            'tmp_dir_identity="$("${safe_fs_cmd[@]}" identity verify-rpm "${tmp_dir}" --kind dir)"',
            source,
        )
        self.assertIn('--expected-identity "${tmp_dir_identity}"', source)

    def test_snapshot_copy_enforces_rpm_size_limit(self) -> None:
        source = VERIFY_RPM.read_text(encoding="utf-8")

        self.assertIn(
            'copy-file verify-rpm "${rpm_path}" "${rpm_snapshot}" 0644 \\\n  --max-bytes "${MAX_RPM_ARCHIVE_BYTES}"',
            source,
        )
        self.assertIn('snapshot_bytes="$(stat -c \'%s\' "${rpm_snapshot}")"', source)


if __name__ == "__main__":
    unittest.main()
