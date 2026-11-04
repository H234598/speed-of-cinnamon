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

    def test_bound_wipe_retries_interrupted_syscalls(self) -> None:
        from unittest import mock
        from speed_of_cinnamon import secure_delete

        with tempfile.TemporaryDirectory(dir="/dev/shm") as tmp:
            path = Path(tmp) / "claim.bin"
            path.write_bytes(b"sensitive")
            descriptor = os.open(path, os.O_RDWR | os.O_CLOEXEC)
            real_pwrite = secure_delete._OS_PWRITE
            real_ftruncate = secure_delete._OS_FTRUNCATE
            real_fsync = secure_delete._OS_FSYNC
            pwrite_calls = 0
            ftruncate_calls = 0
            fsync_calls = 0

            def flaky_pwrite(fd: int, data: bytes, offset: int) -> int:
                nonlocal pwrite_calls
                pwrite_calls += 1
                if pwrite_calls == 1:
                    raise InterruptedError()
                assert callable(real_pwrite)
                return real_pwrite(fd, data, offset)

            def flaky_ftruncate(fd: int, length: int) -> None:
                nonlocal ftruncate_calls
                ftruncate_calls += 1
                if ftruncate_calls == 1:
                    raise InterruptedError()
                real_ftruncate(fd, length)

            def flaky_fsync(fd: int) -> None:
                nonlocal fsync_calls
                fsync_calls += 1
                if fsync_calls == 1:
                    raise InterruptedError()
                real_fsync(fd)

            try:
                with (
                    mock.patch.object(
                        secure_delete, "_OS_PWRITE", side_effect=flaky_pwrite
                    ),
                    mock.patch.object(
                        secure_delete, "_OS_FTRUNCATE", side_effect=flaky_ftruncate
                    ),
                    mock.patch.object(
                        secure_delete, "_OS_FSYNC", side_effect=flaky_fsync
                    ),
                ):
                    wiped = secure_wipe_bound_regular_fd(
                        descriptor,
                        os.fstat(descriptor),
                        field_name="test claim",
                        truncate_after_wipe=True,
                    )
                self.assertEqual(wiped.st_size, 0)
                self.assertEqual(pwrite_calls, 2)
                self.assertEqual(ftruncate_calls, 2)
                self.assertEqual(fsync_calls, 2)
            finally:
                os.close(descriptor)


if __name__ == "__main__":
    unittest.main()
