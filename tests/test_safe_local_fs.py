from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
import os
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "safe-local-fs.py"
MODULE_SPEC = importlib.util.spec_from_file_location("safe_local_fs", SCRIPT)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
SAFE_LOCAL_FS = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(SAFE_LOCAL_FS)


def run_helper(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


class SafeLocalFsTest(unittest.TestCase):
    def test_mkdirs_accepts_concurrent_directory_creation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "nested"
            real_mkdir = SAFE_LOCAL_FS.os.mkdir

            def create_then_report_exists(name: str, mode: int, *, dir_fd: int | None = None) -> None:
                real_mkdir(name, mode, dir_fd=dir_fd)
                raise FileExistsError(name)

            with mock.patch.object(SAFE_LOCAL_FS.os, "mkdir", side_effect=create_then_report_exists):
                fd = SAFE_LOCAL_FS._open_dir_chain(target, action="test", create=True)
            self.assertIsNotNone(fd)
            os.close(fd)
            self.assertTrue(target.is_dir())

    def test_remove_leaf_unlinks_symlink_leaf_without_following_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            target = base / "target.txt"
            target.write_text("keep\n", encoding="utf-8")
            link = base / "link"
            link.symlink_to(target)

            result = run_helper("remove-leaf", "install", str(link))

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stderr, "")
            self.assertFalse(link.exists())
            self.assertTrue(target.exists())

    def test_install_tree_rejects_fifo_source_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            target = base / "target"
            source.mkdir()
            (source / "regular.txt").write_text("ok\n", encoding="utf-8")
            fifo = source / "pipe"
            try:
                os.mkfifo(fifo)
            except OSError as exc:
                self.skipTest(f"fifo unavailable: {exc}")

            result = run_helper("install-tree", "test", str(source), str(target), "test tree")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unsupported file type", result.stderr)
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
