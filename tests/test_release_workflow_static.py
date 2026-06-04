from pathlib import Path
import re


REPO_ROOT = Path(__file__).resolve().parents[1]


def _release_workflow() -> str:
    return (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )


def test_release_workflow_validates_tag_before_checkout():
    workflow = _release_workflow()

    assert "validate-release-tag:" in workflow
    assert "ref: ${{ needs.validate-release-tag.outputs.ref }}" in workflow
    assert "refs/tags/%s" in workflow
    assert "format('refs/tags/{0}', inputs.tag)" not in workflow


def test_release_workflow_run_blocks_do_not_interpolate_tag_input_directly():
    workflow = _release_workflow()
    run_blocks = re.findall(r"(?ms)^\\s+run: \\|\\n((?:^\\s{10,}.*\\n?)*)", workflow)

    assert run_blocks
    for block in run_blocks:
        assert "${{ inputs.tag }}" not in block
