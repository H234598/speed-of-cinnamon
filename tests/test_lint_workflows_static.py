from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class LintWorkflowsStaticTests(unittest.TestCase):
    def test_explicit_actionlint_override_is_private_and_wired_into_make(self) -> None:
        script = (REPO_ROOT / "scripts" / "lint-workflows.sh").read_text(encoding="utf-8")
        makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")

        self.assertIn('actionlint_path_override="${ACTIONLINT_PATH:-}"', script)
        self.assertIn("validate_actionlint_override", script)
        self.assertIn('[[ ! -f "${path}" || -L "${path}" || ! -x "${path}" ]]', script)
        self.assertIn('actionlint override must not be group- or world-writable.', script)
        self.assertIn("ACTIONLINT_PATH ?= $(shell command -v actionlint 2>/dev/null)", makefile)
        self.assertIn("export ACTIONLINT_PATH", makefile)
        self.assertNotIn('ACTIONLINT_PATH="$(ACTIONLINT_PATH)" ./scripts/lint-workflows.sh', makefile)

    def test_actionlint_execution_is_bounded(self) -> None:
        script = (REPO_ROOT / "scripts" / "lint-workflows.sh").read_text(encoding="utf-8")

        self.assertIn("readonly ACTIONLINT_TIMEOUT_SECONDS=60", script)
        self.assertIn("timeout unavailable; refusing unbounded actionlint execution.", script)
        self.assertIn('timeout --signal=TERM --kill-after=2s "${ACTIONLINT_TIMEOUT_SECONDS}s" "${actionlint_path}" "$@"', script)

    def test_yaml_fallback_bounds_and_classifies_workflow_input(self) -> None:
        script = (REPO_ROOT / "scripts" / "lint-workflows.sh").read_text(encoding="utf-8")

        self.assertIn("MAX_WORKFLOW_BYTES = 1 * 1024 * 1024", script)
        self.assertIn("stream.read(MAX_WORKFLOW_BYTES + 1)", script)
        self.assertIn("if len(payload) > MAX_WORKFLOW_BYTES:", script)
        self.assertIn("text = payload.decode('utf-8')", script)
        self.assertIn("class StrictLoader(yaml.SafeLoader):", script)
        self.assertIn("duplicate workflow YAML key", script)
        self.assertIn("yaml.load(text, Loader=StrictLoader)", script)
        self.assertIn("workflow YAML validation failed", script)
        self.assertIn("yaml_status=0", script)
        self.assertIn('if [[ "${yaml_status}" == "2" ]]', script)


if __name__ == "__main__":
    unittest.main()
