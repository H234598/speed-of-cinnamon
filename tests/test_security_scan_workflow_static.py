import unittest
from pathlib import Path
import re


REPO_ROOT = Path(__file__).resolve().parents[1]


def _security_scan_workflow() -> str:
    return (REPO_ROOT / ".github" / "workflows" / "security-scan.yml").read_text(
        encoding="utf-8"
    )


def test_security_scan_validates_ref_before_checkout():
    workflow = _security_scan_workflow()

    assert "validate-scan-ref:" in workflow
    assert "ref: ${{ needs.validate-scan-ref.outputs.ref }}" in workflow
    assert "ref: ${{ inputs.ref || github.ref }}" not in workflow
    assert workflow.count("needs: validate-scan-ref") == 2


def test_security_scan_run_blocks_do_not_interpolate_ref_input_directly():
    workflow = _security_scan_workflow()
    run_blocks = re.findall(r"(?ms)^\\s+run: \\|\\n((?:^\\s{10,}.*\\n?)*)", workflow)

    assert run_blocks
    for block in run_blocks:
        assert "${{ inputs.ref }}" not in block

def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite(tests)
    for name in sorted(globals()):
        value = globals()[name]
        if name.startswith("test_") and callable(value):
            suite.addTest(unittest.FunctionTestCase(value, description=name))
    return suite
