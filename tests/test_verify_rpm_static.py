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

    def test_package_listings_are_bounded_before_parsing(self) -> None:
        source = VERIFY_RPM.read_text(encoding="utf-8")

        self.assertIn("readonly MAX_RPM_LISTING_BYTES=$((16 * 1024 * 1024))", source)
        self.assertIn("payload = handle.read(MAX_RPM_LISTING_BYTES + 1)", source)
        self.assertIn('read_bounded_utf8(file_list, "RPM file listing")', source)
        self.assertIn('read_bounded_utf8(Path(sys.argv[2]), "RPM file metadata")', source)
        self.assertNotIn("file_list.read_text(encoding=\"utf-8\")", source)


if __name__ == "__main__":
    unittest.main()
