import importlib.util
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify-version-consistency.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("verify_version_consistency", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load version consistency script")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VersionConsistencyTests(unittest.TestCase):
    def test_shipped_version_surfaces_match(self) -> None:
        module = _load_module()
        self.assertEqual(module.validate(), [])

    def test_version_is_current_feature_release(self) -> None:
        module = _load_module()
        self.assertEqual(module._project_version(), "0.3.5")

    def test_main_reports_resource_exhaustion_without_traceback(self) -> None:
        module = _load_module()
        for failure in (RecursionError("deep TOML/JSON"), MemoryError("budget")):
            with self.subTest(failure=type(failure).__name__), mock.patch.object(
                module, "validate", side_effect=failure
            ), redirect_stderr(StringIO()) as stderr:
                self.assertEqual(module.main(), 1)
                self.assertIn("version consistency check failed", stderr.getvalue())

    def test_json_loader_rejects_duplicate_keys_and_non_finite_numbers(self) -> None:
        module = _load_module()
        with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
            module._load_json('{"version":"safe","version":"shadowed"}')
        with self.assertRaisesRegex(ValueError, "non-finite JSON value"):
            module._load_json('{"version":NaN}')


if __name__ == "__main__":
    unittest.main()
