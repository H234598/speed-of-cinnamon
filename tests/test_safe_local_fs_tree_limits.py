import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path

from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SAFE_LOCAL_FS = REPO_ROOT / "scripts" / "safe-local-fs.py"


def load_safe_local_fs():
    spec = importlib.util.spec_from_file_location("safe_local_fs_tree_limits", SAFE_LOCAL_FS)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load safe-local-fs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SafeLocalFsTreeLimitTests(unittest.TestCase):
    def test_install_tree_rejects_entry_budget_overflow(self) -> None:
        module = load_safe_local_fs()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            target = root / "target"
            source.mkdir()
            (source / "one.txt").write_text("1", encoding="utf-8")
            (source / "two.txt").write_text("2", encoding="utf-8")
            args = module.argparse.Namespace(
                action="test",
                source=str(source),
                target=str(target),
                label="tree",
            )
            with mock.patch.object(module, "MAX_TREE_ENTRIES", 1):
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    with self.assertRaises(SystemExit) as raised:
                        module.cmd_install_tree(args)
                self.assertEqual(raised.exception.code, 1)
                self.assertIn("too many entries (max 1)", stderr.getvalue())
            self.assertFalse(target.exists())

    def test_install_tree_rejects_file_byte_budget_overflow(self) -> None:
        module = load_safe_local_fs()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            target = root / "target"
            source.mkdir()
            (source / "payload.txt").write_text("payload", encoding="utf-8")
            args = module.argparse.Namespace(
                action="test",
                source=str(source),
                target=str(target),
                label="tree",
            )
            with mock.patch.object(module, "MAX_TREE_FILE_BYTES", 1):
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    with self.assertRaises(SystemExit) as raised:
                        module.cmd_install_tree(args)
                self.assertEqual(raised.exception.code, 1)
                self.assertIn("too many file bytes", stderr.getvalue())
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
