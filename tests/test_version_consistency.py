import importlib.util
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
