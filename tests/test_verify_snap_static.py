from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
VERIFY_SNAP = REPO_ROOT / "scripts" / "verify-snap.sh"


class VerifySnapStaticTest(unittest.TestCase):
    def test_verification_cleanup_requires_expected_identity(self) -> None:
        source = VERIFY_SNAP.read_text(encoding="utf-8")

        self.assertIn(
            'tmp_dir_identity="$("${safe_fs_cmd[@]}" identity verify-snap "${tmp_dir}" --kind dir)"',
            source,
        )
        self.assertIn('--expected-identity "${tmp_dir_identity}"', source)

    def test_verification_requires_runtime_and_rejects_host_bytecode(self) -> None:
        source = VERIFY_SNAP.read_text(encoding="utf-8")

        self.assertIn("REQUIRED_RUNTIME_ENTRIES = {", source)
        self.assertIn('"squashfs-root/usr/bin/python3"', source)
        self.assertIn('"squashfs-root/usr/bin/secret-tool"', source)
        self.assertIn('"squashfs-root/usr/lib/python3/dist-packages/cryptography/__init__.py"', source)
        self.assertIn('"squashfs-root/usr/lib/python3/dist-packages/cryptography/__about__.py"', source)
        self.assertIn("snap cryptography is too old", source)
        self.assertIn("snap package contains stale Python bytecode", source)
        self.assertIn('path_text.endswith(".pyo")', source)

    def test_verification_requires_executable_command_entries(self) -> None:
        source = VERIFY_SNAP.read_text(encoding="utf-8")

        self.assertIn("REQUIRED_EXECUTABLE_ENTRIES = {", source)
        self.assertIn('"squashfs-root/bin/speed-of-cinnamon",', source)
        self.assertIn('"squashfs-root/usr/bin/secret-tool",', source)
        self.assertIn("symbolic_mode_to_octal(seen[required_entry]) != 0o755", source)

    def test_package_listing_is_bounded_before_parsing(self) -> None:
        source = VERIFY_SNAP.read_text(encoding="utf-8")

        self.assertIn("readonly MAX_SNAP_LISTING_BYTES=$((16 * 1024 * 1024))", source)
        self.assertIn("payload = handle.read(MAX_SNAP_LISTING_BYTES + 1)", source)
        self.assertIn('read_bounded_utf8(Path(sys.argv[1]), "snap listing")', source)
        self.assertNotIn("Path(sys.argv[1]).read_text(encoding=\"utf-8\")", source)

    def test_snap_metadata_reads_are_bounded(self) -> None:
        source = VERIFY_SNAP.read_text(encoding="utf-8")

        self.assertIn("MAX_SNAP_METADATA_BYTES = 1 << 20", source)
        self.assertIn("handle.read(MAX_SNAP_METADATA_BYTES + 1)", source)
        self.assertNotIn("about_path.read_text(encoding=\"utf-8\")", source)
        self.assertNotIn("Path(snap_yaml_path).read_text(encoding=\"utf-8\")", source)


if __name__ == "__main__":
    unittest.main()
