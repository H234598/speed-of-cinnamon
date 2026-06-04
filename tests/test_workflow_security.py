from __future__ import annotations

import re
import shlex
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
WORKFLOW_FILES = sorted(
    [
        *WORKFLOW_DIR.glob("*.yml"),
        *WORKFLOW_DIR.glob("*.yaml"),
    ]
)
COMMIT_SHA_RE = re.compile(r"[0-9a-f]{40}")
RUN_LINE_RE = re.compile(r"^(?P<indent>\s*)run:\s*(?P<value>.*)$")
EXPRESSION_RE = re.compile(r"\$\{\{\s*(?P<expr>[^}]+?)\s*\}\}")
ALLOWED_GITHUB_RUN_EXPRESSIONS = {
    "github.event.pull_request.base.sha",
    "github.event.pull_request.head.sha",
    "github.sha",
}


def workflow_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def strip_yaml_scalar(value: str) -> str:
    return value.split("#", 1)[0].strip().strip("\"'")


def run_blocks(text: str) -> list[str]:
    lines = text.splitlines()
    blocks: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        match = RUN_LINE_RE.match(line)
        if match is None:
            index += 1
            continue
        run_indent = len(match.group("indent"))
        value = match.group("value").strip()
        if value not in {"|", ">"}:
            blocks.append(value)
            index += 1
            continue
        index += 1
        block_lines: list[str] = []
        while index < len(lines):
            next_line = lines[index]
            if next_line.strip() and len(next_line) - len(next_line.lstrip(" ")) <= run_indent:
                break
            block_lines.append(next_line)
            index += 1
        blocks.append("\n".join(block_lines))
    return blocks


class WorkflowSecurityTest(unittest.TestCase):
    def test_workflow_files_are_present(self) -> None:
        self.assertGreater(len(WORKFLOW_FILES), 0)

    def test_workflows_do_not_use_pull_request_target(self) -> None:
        for path in WORKFLOW_FILES:
            with self.subTest(workflow=path.name):
                self.assertNotRegex(workflow_text(path), r"(?m)^\s*pull_request_target\s*:")

    def test_workflows_declare_top_level_permissions(self) -> None:
        for path in WORKFLOW_FILES:
            with self.subTest(workflow=path.name):
                self.assertRegex(workflow_text(path), r"(?m)^permissions\s*:")

    def test_external_actions_are_pinned_to_full_commit_sha(self) -> None:
        for path in WORKFLOW_FILES:
            for line_number, line in enumerate(workflow_text(path).splitlines(), start=1):
                if "uses:" not in line:
                    continue
                value = strip_yaml_scalar(line.split("uses:", 1)[1])
                if value.startswith("./"):
                    continue
                with self.subTest(workflow=path.name, line=line_number, uses=value):
                    self.assertIn("@", value)
                    ref = value.rsplit("@", 1)[1]
                    self.assertIsNotNone(COMMIT_SHA_RE.fullmatch(ref))

    def test_run_blocks_do_not_interpolate_untrusted_github_contexts(self) -> None:
        for path in WORKFLOW_FILES:
            for block_index, block in enumerate(run_blocks(workflow_text(path)), start=1):
                for expression in EXPRESSION_RE.finditer(block):
                    expr = " ".join(expression.group("expr").split())
                    if not expr.startswith("github."):
                        continue
                    with self.subTest(workflow=path.name, run_block=block_index, expression=expr):
                        self.assertIn(expr, ALLOWED_GITHUB_RUN_EXPRESSIONS)

    def test_workflow_pip_installs_pin_package_versions(self) -> None:
        for path in WORKFLOW_FILES:
            for block_index, block in enumerate(run_blocks(workflow_text(path)), start=1):
                for line_number, raw_line in enumerate(block.splitlines() or [block], start=1):
                    line = raw_line.strip()
                    if "python -m pip install" not in line:
                        continue
                    parts = shlex.split(line)
                    try:
                        install_index = parts.index("install")
                    except ValueError:
                        continue
                    packages = [
                        part
                        for part in parts[install_index + 1 :]
                        if part and not part.startswith("-")
                    ]
                    with self.subTest(workflow=path.name, run_block=block_index, line=line_number):
                        self.assertGreater(len(packages), 0)
                        for package in packages:
                            self.assertIn("==", package)


if __name__ == "__main__":
    unittest.main()
