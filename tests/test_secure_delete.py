from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from speed_of_cinnamon.secure_delete import (
    secure_wipe_bound_regular_fd,
    secure_wipe_regular_file_at,
)


class SecureDeletePrefixTest(unittest.TestCase):
    def test_bound_wipe_truncates_without_closing_descriptor(self) -> None:
        with tempfile.TemporaryDirectory(dir="/dev/shm") as tmp:
            path = Path(tmp) / "claim.bin"
            path.write_bytes(b"sensitive")
            descriptor = os.open(path, os.O_RDWR | os.O_CLOEXEC)
            try:
                wiped = secure_wipe_bound_regular_fd(
                    descriptor,
                    os.fstat(descriptor),
                    field_name="test claim",
                    truncate_after_wipe=True,
                )
                self.assertEqual(wiped.st_size, 0)
                self.assertEqual(os.fstat(descriptor).st_size, 0)
                self.assertEqual(os.pread(descriptor, 1, 0), b"")
            finally:
                os.close(descriptor)

    def test_wipe_bytes_preserves_committed_trailer(self) -> None:
        with tempfile.TemporaryDirectory(dir="/dev/shm") as tmp:
            root = Path(tmp)
            path = root / "claim.bin"
            path.write_bytes(b"secretCOMMIT")
            parent_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                secure_wipe_regular_file_at(
                    parent_fd,
                    path.name,
                    path.stat(),
                    field_name="test claim",
                    wipe_bytes=6,
                )
            finally:
                os.close(parent_fd)

            self.assertEqual(path.read_bytes(), b"\x00" * 6 + b"COMMIT")


if __name__ == "__main__":
    unittest.main()
