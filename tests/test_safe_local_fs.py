from __future__ import annotations

import importlib.util
import signal
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
    def test_close_failures_are_reported_without_masking_primary_error(self) -> None:
        module = SAFE_LOCAL_FS
        with mock.patch.object(module.os, "close", side_effect=OSError("close failed")):
            with self.assertRaisesRegex(OSError, "descriptor cleanup failed"):
                module._close_fds(123, action="test")

            primary = RuntimeError("primary failure")
            module._close_fds(123, action="test", primary_error=primary)
            self.assertIn("test descriptor cleanup failed", getattr(primary, "__notes__", ()))

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

    def test_copy_file_enforces_max_bytes_before_activation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.txt"
            target = root / "target.txt"
            source.write_bytes(b"12345")
            args = SAFE_LOCAL_FS.argparse.Namespace(
                action="test",
                src=str(source),
                dst=str(target),
                mode="0600",
                dst_must_not_exist=False,
                max_bytes=4,
            )

            with self.assertRaisesRegex(OSError, "copy size limit"):
                SAFE_LOCAL_FS.cmd_copy_file(args)

            self.assertFalse(target.exists())

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

    def test_replace_does_not_clobber_raced_destination(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.txt"
            target = root / "target.txt"
            source.write_text("source\n", encoding="utf-8")
            args = SAFE_LOCAL_FS.argparse.Namespace(
                action="test",
                src=str(source),
                dst=str(target),
                src_kind="file",
                dst_must_not_exist=True,
            )
            real_no_replace = SAFE_LOCAL_FS._rename_without_replacing

            def create_raced_destination(
                source_name: str,
                target_name: str,
                *,
                directory_fd: int,
                target_directory_fd: int | None = None,
                expected_source_stat: os.stat_result | None = None,
                action: str,
            ) -> None:
                target_fd = target_directory_fd if target_directory_fd is not None else directory_fd
                raced_target = Path(f"/proc/self/fd/{target_fd}") / target_name
                raced_target.write_text("raced\n", encoding="utf-8")
                real_no_replace(
                    source_name,
                    target_name,
                    directory_fd=directory_fd,
                    target_directory_fd=target_directory_fd,
                    expected_source_stat=expected_source_stat,
                    action=action,
                )

            with mock.patch.object(SAFE_LOCAL_FS, "_rename_without_replacing", side_effect=create_raced_destination):
                with self.assertRaises(FileExistsError):
                    SAFE_LOCAL_FS.cmd_replace(args)

            self.assertEqual(source.read_text(encoding="utf-8"), "source\n")
            self.assertEqual(target.read_text(encoding="utf-8"), "raced\n")

    def test_replace_expected_missing_uses_atomic_no_clobber(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.txt"
            target = root / "target.txt"
            source.write_text("source\n", encoding="utf-8")
            args = SAFE_LOCAL_FS.argparse.Namespace(
                action="test",
                src=str(source),
                dst=str(target),
                src_kind="file",
                dst_must_not_exist=False,
                expected_dst_identity="missing",
            )
            real_no_replace = SAFE_LOCAL_FS._rename_without_replacing

            def create_raced_destination(
                source_name: str,
                target_name: str,
                *,
                directory_fd: int,
                target_directory_fd: int | None = None,
                expected_source_stat: os.stat_result | None = None,
                action: str,
            ) -> None:
                target_fd = target_directory_fd if target_directory_fd is not None else directory_fd
                raced_target = Path(f"/proc/self/fd/{target_fd}") / target_name
                raced_target.write_text("raced\n", encoding="utf-8")
                real_no_replace(
                    source_name,
                    target_name,
                    directory_fd=directory_fd,
                    target_directory_fd=target_directory_fd,
                    expected_source_stat=expected_source_stat,
                    action=action,
                )

            with mock.patch.object(SAFE_LOCAL_FS, "_rename_without_replacing", side_effect=create_raced_destination):
                with self.assertRaises(FileExistsError):
                    SAFE_LOCAL_FS.cmd_replace(args)

            self.assertEqual(source.read_text(encoding="utf-8"), "source\n")
            self.assertEqual(target.read_text(encoding="utf-8"), "raced\n")

    def test_replace_does_not_move_destination_changed_after_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.txt"
            target = root / "target.txt"
            raced_target = root / "raced-target.txt"
            source.write_text("source\n", encoding="utf-8")
            target.write_text("old\n", encoding="utf-8")
            real_lstat_at = SAFE_LOCAL_FS._lstat_at
            destination_checks = 0

            def lstat_and_replace_destination(parent_fd: int, name: str) -> os.stat_result | None:
                nonlocal destination_checks
                result = real_lstat_at(parent_fd, name)
                if name == target.name and result is not None:
                    destination_checks += 1
                    if destination_checks == 2:
                        target.rename(raced_target)
                        target.write_text("raced\n", encoding="utf-8")
                        return real_lstat_at(parent_fd, name)
                return result

            args = SAFE_LOCAL_FS.argparse.Namespace(
                action="test",
                src=str(source),
                dst=str(target),
                src_kind="file",
                dst_must_not_exist=False,
            )
            with mock.patch.object(SAFE_LOCAL_FS, "_lstat_at", side_effect=lstat_and_replace_destination):
                with self.assertRaisesRegex(OSError, "destination changed"):
                    SAFE_LOCAL_FS.cmd_replace(args)

            self.assertEqual(source.read_text(encoding="utf-8"), "source\n")
            self.assertEqual(target.read_text(encoding="utf-8"), "raced\n")
            self.assertEqual(raced_target.read_text(encoding="utf-8"), "old\n")

    def test_exchange_rejects_changed_result_after_atomic_exchange(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            target = root / "target"
            raced_target = root / "raced-target"
            source.mkdir()
            target.mkdir()
            (source / "payload").write_text("source\n", encoding="utf-8")
            (target / "payload").write_text("target\n", encoding="utf-8")
            source_identity = SAFE_LOCAL_FS._identity_text(source.stat())
            target_identity = SAFE_LOCAL_FS._identity_text(target.stat())
            real_exchange = SAFE_LOCAL_FS._rename_exchange

            def exchange_then_replace_target(
                source_name: str,
                target_name: str,
                *,
                directory_fd: int,
                target_directory_fd: int | None = None,
                action: str,
            ) -> None:
                real_exchange(
                    source_name,
                    target_name,
                    directory_fd=directory_fd,
                    target_directory_fd=target_directory_fd,
                    action=action,
                )
                target_path = Path(f"/proc/self/fd/{target_directory_fd}") / target_name
                target_path.rename(raced_target)
                target_path.mkdir()

            args = SAFE_LOCAL_FS.argparse.Namespace(
                action="test",
                source=str(source),
                target=str(target),
                kind="dir",
                expected_source_identity=source_identity,
                expected_target_identity=target_identity,
            )
            with mock.patch.object(
                SAFE_LOCAL_FS,
                "_rename_exchange",
                side_effect=exchange_then_replace_target,
            ):
                with self.assertRaisesRegex(SystemExit, "1"):
                    SAFE_LOCAL_FS.cmd_exchange(args)

            self.assertEqual((source / "payload").read_text(encoding="utf-8"), "target\n")
            self.assertEqual((raced_target / "payload").read_text(encoding="utf-8"), "source\n")

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

    def test_remove_leaf_unlinks_regular_hardlink_without_touching_sibling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sibling = root / "sibling.txt"
            sibling.write_text("keep\n", encoding="utf-8")
            link = root / "link.txt"
            os.link(sibling, link)

            result = run_helper("remove-leaf", "test", str(link))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(link.exists())
            self.assertEqual(sibling.read_text(encoding="utf-8"), "keep\n")
            self.assertEqual(sibling.stat().st_nlink, 1)

    def test_remove_file_unlinks_symlink_without_following_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.txt"
            target.write_text("keep\n", encoding="utf-8")
            link = root / "link"
            link.symlink_to(target)

            result = run_helper("remove", "test", str(link), "--kind", "file")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(link.exists())
            self.assertTrue(target.exists())

    def test_rmdir_sigkill_claim_keeps_rpm_workspace_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "speed-of-cinnamon-rpm-tmp-workspace"
            target.mkdir()
            child_code = f"""
import importlib.util
import os
import signal
from pathlib import Path
spec = importlib.util.spec_from_file_location('safe_local_fs_child', {str(SCRIPT)!r})
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)
target = Path({str(target)!r})
real_rmdir = module.os.rmdir
def kill_after_claim(name, *, dir_fd=None):
    if isinstance(name, str) and name.startswith(target.name + '.safe-rmdir-'):
        os.kill(os.getpid(), signal.SIGKILL)
    return real_rmdir(name, dir_fd=dir_fd)
module.os.rmdir = kill_after_claim
module.cmd_rmdir(module.argparse.Namespace(
    action='test', path=str(target), expected_identity=None, ignore_non_empty=False,
))
"""
            result = subprocess.run(
                [sys.executable, "-c", child_code],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr)
            claims = list(root.glob(f"{target.name}.safe-rmdir-*"))
            self.assertEqual(len(claims), 1)
            self.assertTrue(claims[0].is_dir())
            claims[0].rmdir()

    def test_partial_directory_cleanup_reports_actual_final_residue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "speed-of-cinnamon-rpm-tmp-workspace"
            target.mkdir()
            (target / "payload").write_text("payload\n", encoding="utf-8")
            args = SAFE_LOCAL_FS.argparse.Namespace(
                action="test",
                path=str(target),
                expected_identity=None,
                kind="dir",
            )
            real_rmdir = SAFE_LOCAL_FS.os.rmdir

            def fail_final(name: object, *, dir_fd: int | None = None) -> None:
                if isinstance(name, str) and name.startswith(target.name + ".final-"):
                    raise OSError("injected final rmdir failure")
                real_rmdir(name, dir_fd=dir_fd)

            with mock.patch.object(SAFE_LOCAL_FS.os, "rmdir", side_effect=fail_final):
                with self.assertRaisesRegex(OSError, "stale cleanup residue remains at"):
                    SAFE_LOCAL_FS.cmd_remove(args)

            self.assertFalse(target.exists())
            residues = list(root.glob(f"{target.name}.final-*"))
            self.assertEqual(len(residues), 1)
            self.assertFalse((residues[0] / "payload").exists())
            residues[0].rmdir()

    def test_identity_reports_device_inode_and_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target.txt"
            target.write_text("safe\n", encoding="utf-8")

            result = run_helper("identity", "test", str(target), "--kind", "file")

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            stat_result = target.stat()
            self.assertEqual(
                result.stdout.strip(),
                f"{stat_result.st_dev}:{stat_result.st_ino}:{stat_result.st_mode}",
            )

    def test_private_chain_accepts_current_secure_home(self) -> None:
        result = run_helper("assert-private-chain", "test", str(Path.home()))

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_private_chain_rejects_writable_euid_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unsafe = root / "writable-parent"
            target = unsafe / "home"
            target.mkdir(parents=True)
            unsafe.chmod(0o777)

            result = run_helper("assert-private-chain", "test", str(target))

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("writable", result.stderr)

    def test_private_chain_allow_missing_checks_nearest_existing_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unsafe = root / "writable-parent"
            unsafe.mkdir()
            unsafe.chmod(0o777)
            target = unsafe / "not-created" / "yet"

            result = run_helper("assert-private-chain", "test", str(target), "--allow-missing")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("writable", result.stderr)

    def test_private_chain_rejects_foreign_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "home"
            target.mkdir()
            current_euid = os.geteuid()
            with mock.patch.object(SAFE_LOCAL_FS.os, "geteuid", return_value=current_euid + 1):
                with self.assertRaisesRegex(OSError, "untrusted owner|not owned"):
                    SAFE_LOCAL_FS._validate_private_dir_chain(target, action="test")

    def test_remove_leaf_expected_identity_preserves_changed_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target.txt"
            target.write_text("foreign\n", encoding="utf-8")

            result = run_helper(
                "remove-leaf",
                "test",
                str(target),
                "--expected-identity",
                "0:0:0",
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("Traceback", result.stderr)
            self.assertIn("destination changed", result.stderr)
            self.assertEqual(target.read_text(encoding="utf-8"), "foreign\n")

    def test_identity_checked_removals_reject_shared_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.chmod(0o777)
            file_target = root / "file.txt"
            file_target.write_text("safe\n", encoding="utf-8")
            directory_target = root / "directory"
            directory_target.mkdir()
            leaf_target = root / "leaf.txt"
            leaf_target.write_text("safe\n", encoding="utf-8")

            cases = [
                ("remove-leaf", leaf_target, ()),
                ("remove", file_target, ("--kind", "file")),
                ("rmdir", directory_target, ()),
            ]
            for command, target, extra_args in cases:
                with self.subTest(command=command):
                    result = run_helper(
                        command,
                        "test",
                        str(target),
                        *extra_args,
                        "--expected-identity",
                        "0:0:0",
                    )

                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("requires private parent", result.stderr)
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

    def test_cleanup_does_not_delete_replaced_cleanup_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = SAFE_LOCAL_FS
            root = Path(tmp)
            temporary = root / ".target.txt.tmp"
            temporary.write_bytes(b"original\n")
            expected_stat = temporary.stat()
            parent_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            real_lstat_at = module._lstat_at
            raced = False

            def replace_cleanup_entry(parent: int, name: str):
                nonlocal raced
                result = real_lstat_at(parent, name)
                if name.endswith(".cleanup") and result is not None and not raced:
                    cleanup_entry = Path(f"/proc/self/fd/{parent}") / name
                    cleanup_entry.unlink()
                    cleanup_entry.write_bytes(b"attacker\n")
                    raced = True
                return result

            try:
                with mock.patch.object(module, "_lstat_at", side_effect=replace_cleanup_entry):
                    module._cleanup_temporary_file(parent_fd, temporary.name, expected_stat, action="test")
            finally:
                os.close(parent_fd)

            self.assertFalse(raced)
            self.assertFalse(temporary.exists())

    def test_cleanup_skips_shared_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = SAFE_LOCAL_FS
            root = Path(tmp)
            temporary = root / ".target.txt.tmp"
            temporary.write_bytes(b"original\n")
            expected_stat = temporary.stat()
            root.chmod(0o777)
            parent_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                module._cleanup_temporary_file(parent_fd, temporary.name, expected_stat, action="test")
            finally:
                os.close(parent_fd)

            self.assertTrue(temporary.exists())

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

    def test_install_tree_excludes_named_entries_before_copying(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            target = base / "target"
            source.mkdir()
            (source / "keep.txt").write_text("keep\n", encoding="utf-8")
            (source / "__pycache__").mkdir()
            (source / "__pycache__" / "leak.pyc").write_bytes(b"cache")
            (source / ".coverage").write_text("coverage\n", encoding="utf-8")

            result = run_helper(
                "install-tree",
                "test",
                str(source),
                str(target),
                "test tree",
                "--exclude-name",
                "__pycache__",
                "--exclude-name",
                ".coverage",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((target / "keep.txt").read_text(encoding="utf-8"), "keep\n")
            self.assertFalse((target / "__pycache__").exists())
            self.assertFalse((target / ".coverage").exists())

    def test_phase_set_remains_bound_to_pinned_workspace_fd_on_path_substitution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            original = root / "workspace-original"
            replacement = root / "workspace-replacement"
            workspace.mkdir()
            real_open_private = SAFE_LOCAL_FS._open_private_directory

            def open_then_replace(path: Path, *, action: str) -> int:
                directory_fd = real_open_private(path, action=action)
                path.rename(original)
                replacement.mkdir()
                return directory_fd

            args = SAFE_LOCAL_FS.argparse.Namespace(
                action="test",
                workspace=str(workspace),
                phase="pre-activation",
            )
            with mock.patch.object(SAFE_LOCAL_FS, "_open_private_directory", side_effect=open_then_replace):
                SAFE_LOCAL_FS.cmd_phase_set(args)

            self.assertEqual((original / ".install-phase").read_bytes(), b"pre-activation\n")
            self.assertFalse((replacement / ".install-phase").exists())

    def test_install_tree_rejects_existing_target_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            target = base / "target"
            source.mkdir()
            (source / "new.txt").write_text("new\n", encoding="utf-8")
            target.mkdir()
            (target / "old.txt").write_text("old\n", encoding="utf-8")

            args = SAFE_LOCAL_FS.argparse.Namespace(
                action="test",
                source=str(source),
                target=str(target),
                label="test tree",
            )
            with self.assertRaisesRegex(SystemExit, "1"):
                SAFE_LOCAL_FS.cmd_install_tree(args)

            self.assertEqual((target / "old.txt").read_text(encoding="utf-8"), "old\n")
            self.assertFalse((target / "new.txt").exists())
            self.assertEqual(list(base.glob(".target.*.install")), [])


if __name__ == "__main__":
    unittest.main()
