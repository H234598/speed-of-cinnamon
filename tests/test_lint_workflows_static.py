from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class LintWorkflowsStaticTests(unittest.TestCase):
    def test_yaml_fallback_bounds_and_classifies_workflow_input(self) -> None:
        script = (REPO_ROOT / "scripts" / "lint-workflows.sh").read_text(encoding="utf-8")

        self.assertIn("MAX_WORKFLOW_BYTES = 1 * 1024 * 1024", script)
        self.assertIn("stream.read(MAX_WORKFLOW_BYTES + 1)", script)
        self.assertIn("if len(payload) > MAX_WORKFLOW_BYTES:", script)
        self.assertIn("text = payload.decode('utf-8')", script)
        self.assertIn("yaml.safe_load(text)", script)
        self.assertIn("workflow YAML validation failed", script)
        self.assertIn("yaml_status=0", script)
        self.assertIn('if [[ "${yaml_status}" == "2" ]]', script)


if __name__ == "__main__":
    unittest.main()
