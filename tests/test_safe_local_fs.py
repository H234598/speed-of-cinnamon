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

    def test_copy_file_opens_source_without_blocking_on_fifo_race(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.txt"
            target = root / "target.txt"
            source.write_text("safe\n", encoding="utf-8")
            real_open = SAFE_LOCAL_FS.os.open
            source_flags: list[int] = []

            def record_source_flags(path: object, flags: int, *args: object, **kwargs: object) -> int:
                if path == source.name:
                    source_flags.append(flags)
                return real_open(path, flags, *args, **kwargs)

            args = SAFE_LOCAL_FS.argparse.Namespace(
                action="test",
                src=str(source),
                dst=str(target),
                mode="0644",
                dst_must_not_exist=False,
            )
            with mock.patch.object(SAFE_LOCAL_FS.os, "open", side_effect=record_source_flags):
                SAFE_LOCAL_FS.cmd_copy_file(args)

            self.assertTrue(source_flags)
            self.assertTrue(source_flags[0] & getattr(SAFE_LOCAL_FS.os, "O_NONBLOCK", 0))
            self.assertEqual(target.read_text(encoding="utf-8"), "safe\n")

    def test_copy_file_does_not_clobber_raced_destination(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.txt"
            target = root / "target.txt"
            source.write_text("source\n", encoding="utf-8")
            args = SAFE_LOCAL_FS.argparse.Namespace(
                action="test",
                src=str(source),
                dst=str(target),
                mode="0600",
                dst_must_not_exist=True,
            )
            real_no_replace = SAFE_LOCAL_FS._rename_without_replacing

            def create_raced_destination(
                source_name: str,
                target_name: str,
                *,
                directory_fd: int,
                action: str,
            ) -> None:
                raced_target = Path(f"/proc/self/fd/{directory_fd}") / target_name
                raced_target.write_text("raced\n", encoding="utf-8")
                real_no_replace(source_name, target_name, directory_fd=directory_fd, action=action)

            with mock.patch.object(SAFE_LOCAL_FS, "_rename_without_replacing", side_effect=create_raced_destination):
                with self.assertRaises(FileExistsError):
                    SAFE_LOCAL_FS.cmd_copy_file(args)

            self.assertEqual(target.read_text(encoding="utf-8"), "raced\n")

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

    def test_atomic_write_preserves_replaced_temp_during_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = SAFE_LOCAL_FS
            root = Path(tmp)
            target = root / "target.txt"

            def replace_then_fail(src: str, _dst: str, **kwargs: object) -> None:
                parent = Path(f"/proc/self/fd/{kwargs['src_dir_fd']}")
                replacement = parent / src
                replacement.unlink()
                replacement.write_bytes(b"replacement\n")
                raise OSError("activation failed")

            with mock.patch.object(module.os, "replace", side_effect=replace_then_fail):
                with self.assertRaisesRegex(OSError, "activation failed"):
                    module._write_bytes_atomic(target, b"new\n", 0o600, action="test")

            temporary_files = list(root.glob(".target.txt.*.tmp"))
            self.assertEqual(len(temporary_files), 1)
            self.assertEqual(temporary_files[0].read_bytes(), b"replacement\n")

    def test_atomic_write_cleans_temp_on_interrupt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = SAFE_LOCAL_FS
            root = Path(tmp)
            target = root / "target.txt"

            with mock.patch.object(module.os, "replace", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    module._write_bytes_atomic(target, b"new\n", 0o600, action="test")

            self.assertEqual(list(root.glob(".target.txt.*.tmp")), [])

    def test_atomic_copy_preserves_replaced_temp_during_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = SAFE_LOCAL_FS
            root = Path(tmp)
            source = root / "source.txt"
            target = root / "target.txt"
            source.write_bytes(b"source\n")

            def replace_then_fail(src: str, _dst: str, **kwargs: object) -> None:
                parent = Path(f"/proc/self/fd/{kwargs['src_dir_fd']}")
                replacement = parent / src
                replacement.unlink()
                replacement.write_bytes(b"replacement\n")
                raise OSError("activation failed")

            args = module.argparse.Namespace(
                action="test",
                src=str(source),
                dst=str(target),
                mode="0600",
                dst_must_not_exist=False,
            )
            with mock.patch.object(module.os, "replace", side_effect=replace_then_fail):
                with self.assertRaisesRegex(OSError, "activation failed"):
                    module.cmd_copy_file(args)

            temporary_files = list(root.glob(".target.txt.*.tmp"))
            self.assertEqual(len(temporary_files), 1)
            self.assertEqual(temporary_files[0].read_bytes(), b"replacement\n")

    def test_atomic_write_rejects_target_created_during_activation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = SAFE_LOCAL_FS
            root = Path(tmp)
            target = root / "target.txt"
            real_lstat_at = module._lstat_at
            injected = False

            def lstat_and_create_target(parent_fd: int, name: str) -> os.stat_result | None:
                nonlocal injected
                result = real_lstat_at(parent_fd, name)
                if name == target.name and result is None and not injected:
                    injected = True
                    target.write_bytes(b"raced target\n")
                return result

            with mock.patch.object(module, "_lstat_at", side_effect=lstat_and_create_target):
                with self.assertRaisesRegex(OSError, "destination changed"):
                    module._write_bytes_atomic(target, b"new\n", 0o600, action="test")

            self.assertEqual(target.read_bytes(), b"raced target\n")
            self.assertEqual(list(root.glob(".target.txt.*.tmp")), [])

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
