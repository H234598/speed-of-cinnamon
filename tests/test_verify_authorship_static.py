from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
VERIFY_AUTHORSHIP = REPO_ROOT / "scripts" / "verify-authorship.sh"


class VerifyAuthorshipStaticTest(unittest.TestCase):
    def test_git_identity_output_is_deterministic(self) -> None:
        source = VERIFY_AUTHORSHIP.read_text(encoding="utf-8")

        self.assertIn('"--no-color",', source)
        self.assertIn('"--no-show-signature",', source)

    def test_forbidden_scan_is_bounded_and_does_not_follow_special_files(self) -> None:
        source = VERIFY_AUTHORSHIP.read_text(encoding="utf-8")

        self.assertIn("FORBIDDEN_SCAN_CHUNK_BYTES = 1 << 20", source)
        self.assertIn("FORBIDDEN_SCAN_OVERLAP_CHARS = 64", source)
        self.assertIn("getattr(os, \"O_NOFOLLOW\", 0)", source)
        self.assertIn("getattr(os, \"O_NONBLOCK\", 0)", source)
        self.assertIn("stat.S_ISREG(os.fstat(fd).st_mode)", source)
        self.assertIn("overlap = candidate[-FORBIDDEN_SCAN_OVERLAP_CHARS:]", source)

    def test_project_metadata_reader_is_bounded_and_no_follow(self) -> None:
        source = VERIFY_AUTHORSHIP.read_text(encoding="utf-8")

        self.assertIn("MAX_PROJECT_METADATA_BYTES = 1 << 20", source)
        self.assertIn('getattr(os, "O_NOFOLLOW", None)', source)
        self.assertIn("os.read(fd, MAX_PROJECT_METADATA_BYTES + 1)", source)
        self.assertIn("project metadata changed while reading", source)


if __name__ == "__main__":
    unittest.main()
