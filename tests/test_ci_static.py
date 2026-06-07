from __future__ import annotations

import concurrent.futures
import subprocess
import re
import shutil
import tempfile
import unittest
import ast
from pathlib import Path
import tarfile

from speed_of_cinnamon import cli


REPO_ROOT = Path(__file__).resolve().parents[1]


def _is_command_sequence(node: ast.AST | None, safe_names: set[str] | None = None) -> bool:
    safe_names = safe_names or set()
    if isinstance(node, (ast.List, ast.Tuple)):
        return True
    if isinstance(node, ast.Name):
        return node.id in safe_names
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"list", "tuple"}:
        return len(node.args) == 1 and isinstance(node.args[0], (ast.List, ast.Tuple))
    return False


def _safe_command_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _is_command_sequence(node.value):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and node.value is not None and _is_command_sequence(node.value):
            if isinstance(node.target, ast.Name):
                names.add(node.target.id)
    return names


def _is_filtered_environment_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_filtered_environment"


def _safe_env_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _is_filtered_environment_call(node.value):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and node.value is not None and _is_filtered_environment_call(node.value):
            if isinstance(node.target, ast.Name):
                names.add(node.target.id)
    return names


def _is_safe_env_argument(node: ast.AST | None, safe_names: set[str] | None = None) -> bool:
    if node is None:
        return True
    safe_names = safe_names or set()
    if isinstance(node, ast.Name):
        return node.id in safe_names
    if isinstance(node, ast.Call):
        return _is_filtered_environment_call(node)
    if not isinstance(node, ast.Dict):
        return False
    for key in node.keys:
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            return False
    for value in node.values:
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            return False
    return True


_SUBPROCESS_CALLS = {"run", "Popen", "call", "check_call", "check_output"}


def _subprocess_aliases(tree: ast.AST) -> tuple[set[str], set[str]]:
    module_names = {"subprocess"}
    direct_call_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "subprocess":
                    module_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            for alias in node.names:
                if alias.name in _SUBPROCESS_CALLS:
                    direct_call_names.add(alias.asname or alias.name)
    return module_names, direct_call_names


def _called_subprocess_name(func: ast.AST, module_names: set[str], direct_call_names: set[str]) -> str | None:
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        if func.value.id in module_names and func.attr in _SUBPROCESS_CALLS:
            return func.attr
    if isinstance(func, ast.Name) and func.id in direct_call_names:
        return func.id
    return None


def _subprocess_security_offenders(path: Path, tree: ast.AST) -> list[str]:
    offenders: list[str] = []
    module_names, direct_call_names = _subprocess_aliases(tree)
    safe_command_names = _safe_command_names(tree)
    safe_env_names = _safe_env_names(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call_name = _called_subprocess_name(node.func, module_names, direct_call_names)
        if call_name is None:
            continue

        for kw in node.keywords:
            if kw.arg == "shell":
                if not (isinstance(kw.value, ast.Constant) and kw.value.value is False):
                    offenders.append(f"{path}: {call_name} with unsupported shell value")
                continue
            if kw.arg == "env":
                if not _is_safe_env_argument(kw.value, safe_env_names):
                    offenders.append(f"{path}: {call_name} env must be a prepared value or string literal dict")

        command = None
        if node.args:
            command = node.args[0]
        else:
            for kw in node.keywords:
                if kw.arg == "args":
                    command = kw.value
                    break
        if isinstance(command, ast.Constant) and isinstance(command.value, str):
            offenders.append(f"{path}: {call_name} first arg is string constant")
        elif isinstance(command, ast.JoinedStr):
            offenders.append(f"{path}: {call_name} first arg is f-string")
        elif not _is_command_sequence(command, safe_command_names):
            offenders.append(f"{path}: {call_name} command must be list/tuple")
    return offenders


def _workflow_block_lines(text: str, header: str) -> list[str]:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip() == header:
            header_indent = len(line) - len(line.lstrip(" "))
            start = index + 1
            end = start
            while end < len(lines):
                candidate = lines[end]
                if candidate and (len(candidate) - len(candidate.lstrip(" ")) <= header_indent):
                    break
                end += 1
            return [candidate for candidate in lines[start:end] if candidate.strip()]
    raise AssertionError(f"missing workflow block: {header}")


class CiStaticTest(unittest.TestCase):
    def test_makefile_has_repo_local_clean_target(self) -> None:
        makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")

        self.assertIn("clean", makefile.splitlines()[0])
        self.assertIn("clean:\n", makefile)
        self.assertIn("rm -rf -- build dist reports .coverage .pytest_cache .mypy_cache *.egg-info", makefile)
        self.assertIn("find src tests -type d -name __pycache__ -prune -exec rm -rf -- {} +", makefile)
        self.assertIn("find src tests -type f \\( -name '*.pyc' -o -name '*.pyo' \\) -delete", makefile)
        self.assertNotIn("~/.local", makefile)

    def test_cli_reference_and_manpage_cover_subcommands(self) -> None:
        parser = cli.build_parser()
        subcommands: list[str] = []
        for action in parser._actions:
            if action.__class__.__name__ == "_SubParsersAction":
                subcommands = sorted(action.choices)
                break

        self.assertTrue(subcommands)
        docs = {
            "docs/cli-reference.md": (REPO_ROOT / "docs" / "cli-reference.md").read_text(encoding="utf-8"),
            "docs/man/speed-of-cinnamon.1": (REPO_ROOT / "docs" / "man" / "speed-of-cinnamon.1").read_text(
                encoding="utf-8"
            ),
        }
        for command in subcommands:
            if command in {"start", "stop", "toggle", "cancel", "status"}:
                continue
            for path, text in docs.items():
                self.assertIn(command, text, f"{command} missing from {path}")

        project_version = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8").split('version = "')[1].split('"')[0]
        self.assertIn(f"speed-of-cinnamon {project_version}", docs["docs/man/speed-of-cinnamon.1"])
        parser = cli.build_parser()
        subparser_action = next(action for action in parser._actions if action.__class__.__name__ == "_SubParsersAction")
        toggle_help = subparser_action.choices["toggle"].format_help()
        transcribe_file_help = subparser_action.choices["transcribe-file"].format_help()
        for help_text in (toggle_help, transcribe_file_help):
            self.assertIn("keyring fails closed", help_text)
            self.assertIn("Secret Service", help_text)
            self.assertIn("unavailable", help_text)
            self.assertIn("choose passphrase", help_text)
            self.assertIn("explicitly when needed", help_text)
            self.assertNotIn("keyring falls back to", help_text)
        self.assertIn("fails closed if the Secret Service is unavailable", docs["docs/man/speed-of-cinnamon.1"])
        self.assertNotIn("falls back to passphrase mode only when an explicit passphrase source is", docs["docs/man/speed-of-cinnamon.1"])
        self.assertNotIn("falls back to passphrase mode when\nkeyring access fails", docs["docs/man/speed-of-cinnamon.1"])

    def test_command_chain_security_tests_are_present(self) -> None:
        command_chain_test = REPO_ROOT / "tests" / "test_command_chain.py"
        self.assertTrue(command_chain_test.exists(), "command chain security tests must exist")
        text = command_chain_test.read_text(encoding="utf-8")
        self.assertIn("class CommandChainTest", text)
        self.assertIn("unsupported shell operator", text)

    def test_recorder_stop_paths_require_process_identity_guard(self) -> None:
        source = (REPO_ROOT / "src" / "speed_of_cinnamon" / "cli.py").read_text(encoding="utf-8")
        stop_start = source.index("def command_stop(")
        cancel_start = source.index("def command_cancel(")
        next_command = source.index("def command_toggle(", cancel_start)
        stop_block = source[stop_start:cancel_start]
        cancel_block = source[cancel_start:next_command]

        for block in (stop_block, cancel_block):
            self.assertIn("_recording_process_verified_alive(state)", block)
            self.assertIn("stop_process(", block)
            self.assertIn("_coerce_int(state.pid, field_name=\"state pid\")", block)
            self.assertIn("expected_process_identity=state.process_identity", block)
            self.assertLess(
                block.index("_recording_process_verified_alive(state)"),
                block.index("stop_process("),
            )
            self.assertNotIn("stop_process(state.pid)", block)

    def test_runtime_and_release_code_do_not_execute_subprocess_with_shell_strings(self) -> None:
        code_roots = [REPO_ROOT / "src", REPO_ROOT / "scripts"]
        offenders = []
        for code_root in code_roots:
            for path in code_root.rglob("*.py"):
                if "__pycache__" in path.parts:
                    continue
                tree = ast.parse(path.read_text(encoding="utf-8"))
                offenders.extend(_subprocess_security_offenders(path, tree))

        self.assertFalse(offenders, f"unsafe subprocess usage found: {offenders}")

    def test_release_version_tests_do_not_execute_subprocess_with_shell_strings(self) -> None:
        path = REPO_ROOT / "tests" / "test_next_version.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders = _subprocess_security_offenders(path, tree)

        self.assertFalse(offenders, f"unsafe subprocess usage found: {offenders}")

    def test_command_sequence_validation_accepts_supported_forms(self) -> None:
        allowed = ["[\"a\", \"b\"]", "(\"a\", \"b\")", "list([\"a\", \"b\"])", "tuple((\"a\", \"b\"))"]
        blocked = ["command", "tuple('a',)", "list()", "list('ab')", "tuple(command)"]

        for expr in allowed:
            node = ast.parse(expr, mode="eval").body
            self.assertTrue(_is_command_sequence(node))
        for expr in blocked:
            node = ast.parse(expr, mode="eval").body
            self.assertFalse(_is_command_sequence(node))

    def test_subprocess_env_literal_requirement(self) -> None:
        allowed = ["{\"A\": \"B\", \"C\": \"D\"}", "_filtered_environment()"]
        blocked = ["env", "{\"A\": b, \"C\": os.environ['C']}", "dict(a='b')", "os.environ.copy()", "{k: v for k, v in vars.items()}"]

        for expr in allowed:
            node = ast.parse(expr, mode="eval").body
            self.assertTrue(_is_safe_env_argument(node))
        for expr in blocked:
            node = ast.parse(expr, mode="eval").body
            self.assertFalse(_is_safe_env_argument(node))

    def test_subprocess_static_scan_detects_alias_imports(self) -> None:
        tree = ast.parse(
            "from subprocess import run as execute\n"
            "import subprocess as sp\n"
            "execute('echo unsafe', shell=True)\n"
            "sp.Popen('echo unsafe')\n"
        )

        offenders = _subprocess_security_offenders(Path("sample.py"), tree)

        self.assertTrue(any("unsupported shell value" in offender for offender in offenders))
        self.assertTrue(any("Popen first arg is string constant" in offender for offender in offenders))

    def test_shell_and_workflow_files_avoid_high_risk_shell_patterns(self) -> None:
        roots = [REPO_ROOT / "scripts", REPO_ROOT / ".github" / "workflows"]
        applet = REPO_ROOT / "files" / "speed-of-cinnamon@H234598" / "applet.js"
        allowed_applet_shell_lines = {
            '"    sudo dnf install -y ollama",',
            '"    sudo apt-get install -y ollama",',
            '"if command -v dnf >/dev/null 2>&1; then sudo dnf install -y zenity xdotool xclip xsel wl-clipboard pipewire-utils pulseaudio-utils alsa-utils python3-pip; fi",',
        }
        offenders: list[str] = []
        files = [
            path
            for root in roots
            for path in root.rglob("*")
            if not path.is_dir() and path.suffix in {".sh", ".yml", ".yaml"}
        ]
        files.append(applet)
        for path in files:
            text = path.read_text(encoding="utf-8")
            if re.search(r"curl\b[^\n|]*\|\s*(?:sh|bash)\b", text):
                offenders.append(f"{path}: curl piped to shell")
            for line in text.splitlines():
                stripped = line.strip()
                for pattern in ("eval ", "bash -c", "sh -c", "rm -rf /"):
                    if pattern in stripped and not (path == applet and stripped in allowed_applet_shell_lines):
                        offenders.append(f"{path}: high-risk shell pattern {pattern!r}")
                if path == applet:
                    for pattern in ("sudo dnf install -y", "sudo apt-get install -y"):
                        if pattern in stripped and stripped not in allowed_applet_shell_lines:
                            offenders.append(f"{path}: high-risk shell pattern {pattern!r}")
            if 'rm -rf -- "${stage_root}"' in text:
                offenders.append(f"{path}: install cleanup must use safe_fs")
            if 'rm -rf -- "${python_dir}"' in text:
                offenders.append(f"{path}: uninstall target cleanup must use safe_fs")
        self.assertEqual(offenders, [], f"high-risk shell/workflow patterns found: {offenders}")

        applet_text = applet.read_text(encoding="utf-8")
        self.assertNotIn("sudo rm -rf /usr/share/ollama", applet_text)
        self.assertIn("Leaving /usr/share/ollama in place; inspect and remove it manually if desired.", applet_text)

        publish_script = (REPO_ROOT / "scripts" / "publish-github-release.sh").read_text(encoding="utf-8")
        verify_script = (REPO_ROOT / "scripts" / "verify-rpm.sh").read_text(encoding="utf-8")

        self.assertIn('readonly RELEASE_TARGET_REPOSITORY="H234598/speed-of-cinnamon"', publish_script)
        self.assertIn("if [[ -z \"${repo}\" && -z \"${remote_repo}\" ]]; then", publish_script)
        self.assertIn("rpm -qp --scripts", verify_script)
        self.assertIn("rpm -qp --triggers", verify_script)
        self.assertIn("{FILECAPS}", verify_script)
        self.assertRegex(verify_script, r"mode\s*&\s*0o6000")

    def test_runtime_code_does_not_read_full_environment(self) -> None:
        src_root = REPO_ROOT / "src"
        offenders: list[str] = []
        for path in src_root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            if "os.environ.copy(" in text:
                offenders.append(str(path))
        self.assertEqual(offenders, [], f"os.environ.copy should not be used in runtime code: {offenders}")

    def test_runtime_code_has_no_shell_invocations(self) -> None:
        patterns = [
            "shell=True",
            "shell = True",
            "os.system(",
            "subprocess.call(",
        ]
        src_root = REPO_ROOT / "src"
        offenders: list[str] = []
        for path in src_root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            if any(pattern in text for pattern in patterns):
                offenders.append(str(path))
        offenders = sorted(set(offenders))
        self.assertEqual(offenders, [], f"shell-like runtime execution should be avoided: {offenders}")

    def test_runtime_command_resolver_prefers_trusted_path(self) -> None:
        files = [
            "src/speed_of_cinnamon/command_chain.py",
            "src/speed_of_cinnamon/cli.py",
            "src/speed_of_cinnamon/output.py",
            "src/speed_of_cinnamon/doctor.py",
            "src/speed_of_cinnamon/recorder.py",
            "src/speed_of_cinnamon/transcriber.py",
        ]
        for rel_path in files:
            path = REPO_ROOT / rel_path
            self.assertTrue(path.exists(), f"missing expected file: {path}")
            text = path.read_text(encoding="utf-8")
            self.assertIn('_TRUSTED_COMMAND_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"', text)
            self.assertIn('def _which(command_name: str) -> str | None:', text)
            self.assertIn("shutil.which(command_name, path=_TRUSTED_COMMAND_PATH)", text)
            self.assertNotIn("SPEED_OF_CINNAMON_TRUSTED_PATH", text)

    def test_ci_uploads_release_and_rpm_artifacts(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        bandit_requirements = (REPO_ROOT / ".github" / "requirements" / "ci-bandit.txt").read_text(encoding="utf-8")
        coverage_requirements = (REPO_ROOT / ".github" / "requirements" / "ci-coverage.txt").read_text(encoding="utf-8")
        security_workflow = (REPO_ROOT / ".github" / "workflows" / "security-scan.yml").read_text(encoding="utf-8")

        self.assertIn("workflow-lint:", workflow)
        self.assertIn("spelling-lint:", workflow)
        self.assertIn("workflow-change-detection:", workflow)
        self.assertIn("needs: workflow-lint", workflow)
        self.assertIn("needs: workflow-change-detection", workflow)
        self.assertIn("uses: crate-ci/typos@f8a58b6b53f2279f71eb605f03a4ae4d10608f45", workflow)
        self.assertIn("config: ./typos.toml", workflow)
        self.assertIn("security-scan:", workflow)
        self.assertIn("uses: ./.github/workflows/security-scan.yml", workflow)
        self.assertIn(
            "needs:\n"
            "      - workflow-lint\n"
            "      - spelling-lint\n"
            "      - security-scan",
            workflow,
        )
        self.assertIn("- spelling-lint", workflow)
        self.assertIn('ACTIONLINT_STRICT: "true"', workflow)
        self.assertIn("build_generic_rpm:", workflow)
        self.assertIn("fetch-depth: 0", workflow)
        self.assertIn(
            "run: python -m pip install --disable-pip-version-check --no-cache-dir --require-hashes -r .github/requirements/ci-coverage.txt",
            workflow,
        )
        self.assertIn("--require-hashes", bandit_requirements)
        self.assertIn("bandit==1.9.4", bandit_requirements)
        self.assertIn("coverage==7.14.1", coverage_requirements)
        self.assertIn("run: make check", workflow)
        self.assertIn("run: make coverage", workflow)
        self.assertNotIn("QLTY_COVERAGE_TOKEN: ${{ secrets.QLTY_COVERAGE_TOKEN }}", workflow)
        self.assertNotIn("curl -fsSL https://qlty.sh | sh", workflow)
        self.assertNotIn("https://qlty.sh", workflow)
        self.assertNotIn("sh \"${qlty_installer}\"", workflow)
        self.assertIn(
            "uses: qltysh/qlty-action/coverage@fd52dc852530a708d68c3b7342f8d33d1df4cd55 # v2.2.1",
            workflow,
        )
        self.assertIn("if: ${{ github.event_name == 'push' && secrets.QLTY_COVERAGE_TOKEN != '' }}", workflow)
        self.assertIn("token: ${{ secrets.QLTY_COVERAGE_TOKEN }}", workflow)
        self.assertIn("files: reports/lcov.info", workflow)
        self.assertIn("format: lcov", workflow)
        self.assertIn("cli-version: 0.630.0", workflow)
        self.assertIn("sudo apt-get update", workflow)
        self.assertIn("sudo apt-get install -y cpio rpm shellcheck squashfs-tools", workflow)
        self.assertIn("if ! command -v -- snapcraft", workflow)
        self.assertIn("run: make rpm-check", workflow)
        self.assertIn("run: make rpm-generic", workflow)
        self.assertIn("build_generic_rpm=false", workflow)
        self.assertIn("run: make rpm-generic-check", workflow)
        self.assertTrue("if: env.BUILD_GENERIC_RPM == 'true'" in workflow or "if: env.BUILD_GENERIC_RPM == '1'" in workflow)
        self.assertIn("name: Build Snap package", workflow)
        self.assertTrue("if: env.BUILD_SNAP == 'true'" in workflow or "if: env.BUILD_SNAP == '1'" in workflow)
        self.assertIn("uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7", workflow)
        self.assertIn("name: speed-of-cinnamon-source-${{ github.sha }}", workflow)
        self.assertIn("dist/speed-of-cinnamon-*.tar.gz", workflow)
        self.assertIn("dist/speed-of-cinnamon-*.tar.gz.sha256", workflow)
        self.assertIn("name: speed-of-cinnamon-rpm-${{ github.sha }}", workflow)
        self.assertIn("dist/rpmbuild/RPMS/**/*.rpm", workflow)
        self.assertIn("dist/rpmbuild/SRPMS/**/*.rpm", workflow)
        self.assertIn("name: speed-of-cinnamon-generic-rpm-${{ github.sha }}", workflow)
        self.assertIn("dist/rpmbuild-generic/RPMS/**/*.rpm", workflow)
        self.assertIn("dist/rpmbuild-generic/SRPMS/**/*.rpm", workflow)
        self.assertIn("name: speed-of-cinnamon-snap-${{ github.sha }}", workflow)
        self.assertIn("dist/snap/*.snap", workflow)
        self.assertEqual(workflow.count("if-no-files-found: error"), 4)
        self.assertIn("uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("SNAPCRAFT_BASE<<SNAPCRAFT_BASE", workflow)
        self.assertIn("runs-on: ubuntu-24.04", workflow)
        self.assertNotIn("runs-on: ubuntu-latest", workflow)
        self.assertNotIn("echo \"GITHUB_OUTPUT", workflow)
        self.assertNotIn("echo 'GITHUB_OUTPUT", workflow)
        self.assertNotIn("echo \"GITHUB_ENV", workflow)
        self.assertNotIn("echo 'GITHUB_ENV", workflow)
        self.assertNotIn("::set-output", workflow)
        self.assertNotIn("::set-state", workflow)

        codacy_workflow = (REPO_ROOT / ".github" / "workflows" / "codacy.yml").read_text(encoding="utf-8")
        self.assertIn("runs-on: ubuntu-24.04", codacy_workflow)
        self.assertIn(
            "python -m pip install --disable-pip-version-check --no-cache-dir --require-hashes -r .github/requirements/ci-bandit.txt",
            codacy_workflow,
        )
        self.assertIn("python -m bandit -q -r src/speed_of_cinnamon -x tests -f sarif -o results.sarif --exit-zero", codacy_workflow)
        self.assertIn("uses: github/codeql-action/upload-sarif@a6fd1787519fd23e68309fad43738e41a6ff2a9d # v4", codacy_workflow)
        self.assertNotIn("codacy/codacy-analysis-cli-action", codacy_workflow)
        self.assertNotIn("ubuntu-latest", codacy_workflow)

        self.assertIn("name: Security Scan", security_workflow)
        self.assertIn("workflow_call:", security_workflow)
        self.assertIn("workflow_dispatch:", security_workflow)
        self.assertIn("inputs:\n      ref:\n        required: false\n        type: string", security_workflow)
        self.assertIn(
            'workflow_dispatch:\n    inputs:\n      ref:\n        description: "Git ref to scan"\n        required: false\n        type: string',
            security_workflow,
        )
        self.assertIn("validate-scan-ref:", security_workflow)
        self.assertEqual(
            security_workflow.count("ref: ${{ needs.validate-scan-ref.outputs.ref }}"),
            2,
        )
        self.assertNotIn("ref: ${{ inputs.ref || github.ref }}", security_workflow)
        self.assertIn("timeout-minutes: 15", security_workflow)
        self.assertIn("timeout-minutes: 10", security_workflow)
        self.assertIn("python-security:", security_workflow)
        self.assertIn("shell-security:", security_workflow)
        self.assertIn(
            "run: python -m pip install --disable-pip-version-check --no-cache-dir --require-hashes -r .github/requirements/ci-bandit.txt",
            security_workflow,
        )
        self.assertIn("run: make python-security-scan", security_workflow)
        self.assertIn("run: make shell-security-scan", security_workflow)
        self.assertIn("run: |\n          sudo apt-get update\n          sudo apt-get install -y --no-install-recommends shellcheck", security_workflow)
        self.assertIn("permissions:", security_workflow)
        self.assertIn("contents: read", security_workflow)
        self.assertNotIn("contents: write", security_workflow)

    def test_release_workflow_validates_tag_against_pyproject_version(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

        self.assertIn('tags:\n      - "v*.*.*"', workflow)
        checkout_ref = "ref: ${{ needs.validate-release-tag.outputs.ref }}"
        self.assertEqual(workflow.count(checkout_ref), 4)
        self.assertNotIn("format('refs/tags/{0}', inputs.tag)", workflow)
        self.assertIn("validate-release-tag:", workflow)
        self.assertIn("with:\n      ref: ${{ needs.validate-release-tag.outputs.ref }}", workflow)
        self.assertNotIn("format('refs/tags/{0}', inputs.tag)", workflow)
        self.assertNotIn("if [[ ! \"${tag}\" =~ ^v[0-9]+(\\.[0-9]+){0,2}([0-9A-Za-z.+-]*)?$ ]]", workflow)
        self.assertIn('if [[ ! "${tag}" =~ ^v[0-9]+\\.[0-9]+\\.[0-9]+$ ]]; then', workflow)
        self.assertIn("project_version=\"$(python3 -c 'import pathlib, tomllib;", workflow)
        self.assertIn('pathlib.Path("pyproject.toml").read_text(encoding="utf-8")', workflow)
        self.assertIn('expected_tag="v${project_version}"', workflow)
        self.assertIn('if [[ "${tag}" != "${expected_tag}" ]]; then', workflow)
        self.assertIn(
            'printf \'release tag mismatch: workflow triggered with %s but pyproject.toml is %s\\n\' "${tag}" "${expected_tag}" >&2',
            workflow,
        )

    def test_shell_scripts_have_security_preamble(self) -> None:
        offenders: list[str] = []
        for path in sorted((REPO_ROOT / "scripts").glob("*.sh")):
            text = path.read_text(encoding="utf-8")
            if not text.startswith("#!/usr/bin/env bash\n"):
                offenders.append(f"{path}: missing bash shebang")
            if "set -euo pipefail" not in text:
                offenders.append(f"{path}: missing set -euo pipefail")
            if "IFS=$'\\n\\t'" not in text:
                offenders.append(f"{path}: missing strict IFS")

        self.assertEqual(offenders, [])

    def test_release_publish_does_not_clobber_existing_assets(self) -> None:
        publish_script = (REPO_ROOT / "scripts" / "publish-github-release.sh").read_text(encoding="utf-8")
        self.assertIn('readonly TRUSTED_COMMAND_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"', publish_script)
        self.assertIn('export PATH="${TRUSTED_COMMAND_PATH}"', publish_script)
        self.assertIn("contains_control_chars() {", publish_script)
        self.assertIn("0x80 <= ord(char) <= 0x9F for char in value", publish_script)
        self.assertIn("0xDC80 <= ord(char) <= 0xDCFF for char in value", publish_script)
        self.assertNotIn("asset name must not contain control characters: %s", publish_script)
        self.assertNotIn("${asset}\" == *$'\\n'* || \"${asset}\" == *$'\\r'* || \"${asset}\" == *$'\\t'*", publish_script)
        self.assertIn("gh release upload", publish_script)
        self.assertNotIn("--clobber", publish_script)
        self.assertIn("--json assets", publish_script)
        self.assertIn("--json isDraft", publish_script)
        self.assertIn(".assets[].name", publish_script)
        self.assertIn("release asset already exists", publish_script)
        self.assertIn("--draft", publish_script)
        self.assertIn("gh release edit \"${tag}\" \\", publish_script)
        self.assertIn("cleanup_release_state() {", publish_script)
        self.assertIn("mark_release_mutation()", publish_script)
        self.assertIn("publish_release_succeeded()", publish_script)
        self.assertIn("rollback_release_state", publish_script)
        self.assertIn('gh release delete "${tag}" --repo "${repo}" --yes', publish_script)
        self.assertIn('gh release delete-asset "${tag}" "${asset_name}" --repo "${repo}" --yes', publish_script)
        self.assertIn("uploaded_asset_names=()", publish_script)
        self.assertIn('uploaded_asset_names+=("${staged_name}")', publish_script)
        self.assertIn("declare -A staged_names_seen=()", publish_script)
        self.assertIn('duplicate release asset staging name: %s\\n', publish_script)
        self.assertIn('staged_names_seen["${staged_name}"]=1', publish_script)
        self.assertIn("existing_release_title=", publish_script)
        self.assertIn("existing_release_title_captured=", publish_script)
        self.assertIn("existing_notes_file=", publish_script)
        self.assertIn("existing_was_prerelease=", publish_script)
        self.assertIn("--json isPrerelease", publish_script)
        self.assertIn(".name // empty", publish_script)
        self.assertIn("--prerelease", publish_script)
        self.assertIn("--prerelease=false", publish_script)
        self.assertNotIn("--latest", publish_script)
        self.assertIn("failed to snapshot existing release notes for rollback", publish_script)
        self.assertIn('gh release edit "${tag}" \\', publish_script)
        self.assertIn("--draft=false", publish_script)
        self.assertNotIn('gh release edit "${tag}" --repo "${repo}" --draft=false >/dev/null 2>&1 || true', publish_script)

    def test_release_scripts_use_safe_local_fs_for_risky_mutations(self) -> None:
        trusted_path_preamble = (
            'readonly TRUSTED_COMMAND_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"\n'
            'export PATH="${TRUSTED_COMMAND_PATH}"'
        )
        snap_trusted_path_preamble = (
            'readonly TRUSTED_COMMAND_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin:/var/lib/snapd/snap/bin"\n'
            'export PATH="${TRUSTED_COMMAND_PATH}"'
        )
        build_dist = (REPO_ROOT / "scripts" / "build-dist.sh").read_text(encoding="utf-8")
        build_rpm = (REPO_ROOT / "scripts" / "build-rpm.sh").read_text(encoding="utf-8")
        build_snap = (REPO_ROOT / "scripts" / "build-snap.sh").read_text(encoding="utf-8")
        verify_dist = (REPO_ROOT / "scripts" / "verify-dist.sh").read_text(encoding="utf-8")
        verify_rpm = (REPO_ROOT / "scripts" / "verify-rpm.sh").read_text(encoding="utf-8")
        verify_snap = (REPO_ROOT / "scripts" / "verify-snap.sh").read_text(encoding="utf-8")
        uninstall_local = (REPO_ROOT / "scripts" / "uninstall-local.sh").read_text(encoding="utf-8")

        for script_text in (build_dist, build_rpm, verify_dist, verify_rpm, verify_snap):
            self.assertIn(trusted_path_preamble, script_text)
        self.assertIn(snap_trusted_path_preamble, build_snap)
        self.assertIn('safe_fs_cmd=(python3 "${safe_fs}")', build_rpm)
        self.assertIn("activate_with_finalize_lock() {", build_rpm)
        self.assertIn('[sys.executable, safe_fs, "install-tree", "build-rpm", staging_path, final_path, "RPM build directory"]', build_rpm)
        self.assertIn('require_regular_source_file "${tarball}" "tarball source"', build_rpm)
        self.assertIn('require_regular_source_file "${spec_source}" "spec source"', build_rpm)
        self.assertIn('if ! "${safe_fs_cmd[@]}" copy-file build-rpm "${tarball}" "${stage_topdir}/SOURCES/$(basename "${tarball}")" 0644; then', build_rpm)
        self.assertIn('if ! "${safe_fs_cmd[@]}" copy-file build-rpm "${spec_source}" "${spec_file}" 0644; then', build_rpm)
        self.assertNotIn('mv -T -- "${stage_topdir}" "${final_topdir}"', build_rpm)
        self.assertIn('safe_fs_cmd=(python3 "${safe_fs}")', build_snap)
        self.assertIn('snap_workspace="$(mktemp -d "${repo_tmp_root}/speed-of-cinnamon-snap-tree-XXXXXX")"', build_snap)
        self.assertIn('repo_tmp_root="${repo_tmp_abs}"', build_snap)
        self.assertIn('snap_workspace_abs="$(realpath "${snap_workspace}")', build_snap)
        self.assertIn('temporary snap workspace escaped temporary root', build_snap)
        self.assertIn('"${safe_fs_cmd[@]}" remove build-snap "${snap_workspace}" --kind dir', build_snap)
        self.assertIn('"${safe_fs_cmd[@]}" remove build-snap "${snap_workspace_dist}" --kind dir', build_snap)
        self.assertIn('install-tree build-snap "${repo_dir}/snap" "${snap_workspace}/snap" "snap source tree"', build_snap)
        self.assertIn('install-tree build-snap "${repo_dir}/src" "${snap_workspace}/src" "Python source tree"', build_snap)
        self.assertIn('copy-file build-snap "${repo_dir}/pyproject.toml" "${snap_workspace}/pyproject.toml" 0644', build_snap)
        self.assertIn('copy-file build-snap "${repo_dir}/README.md" "${snap_workspace}/README.md" 0644', build_snap)
        self.assertNotIn('install-tree build-snap "${repo_dir}" "${snap_workspace}" "snap temporary source tree"', build_snap)
        self.assertIn('python3 - "${snapcraft_file_rendered}" "${snapcraft_file_rendered}" "${version}" "${snapcraft_base}"', build_snap)
        self.assertIn('( cd "${snap_workspace}" && umask 022 && snapcraft pack --destructive-mode )', build_snap)
        self.assertIn('"${safe_fs_cmd[@]}" copy-file build-snap "${snap_files[0]}" "${output_path}" 0644 --dst-must-not-exist', build_snap)
        self.assertNotIn('rm -rf -- "${snap_workspace}"', build_snap)
        self.assertNotIn("mv -T", build_snap)
        self.assertIn("contains_control_chars() {", verify_dist)
        self.assertIn("0x80 <= ord(char) <= 0x9F for char in value", verify_dist)
        self.assertIn("0xDC80 <= ord(char) <= 0xDCFF for char in value", verify_dist)
        self.assertIn("0x80 <= ord(char) <= 0x9F for char in member.name", verify_dist)
        self.assertIn("0xDC80 <= ord(char) <= 0xDCFF for char in member.name", verify_dist)
        self.assertIn("0x80 <= ord(char) <= 0x9F for char in entry", verify_rpm)
        self.assertIn("0xDC80 <= ord(char) <= 0xDCFF for char in entry", verify_rpm)
        self.assertIn('for entry in file_list.read_text(encoding="utf-8").split("\\n"):', verify_rpm)
        self.assertNotIn(".splitlines()", verify_rpm)
        self.assertNotIn("entry.strip()", verify_rpm)
        self.assertNotIn("entry = raw.strip()", verify_rpm)
        self.assertIn("contains_unsafe_text(path_text)", verify_snap)
        self.assertIn("contains_unsafe_text(path_text)", verify_snap)
        self.assertIn('for raw in Path(sys.argv[1]).read_text(encoding="utf-8").split("\\n"):', verify_snap)
        self.assertNotIn(".splitlines()", verify_snap)
        self.assertNotIn("raw.strip()", verify_snap)
        self.assertNotIn("${tarball_input}\" == *$'\\n'* || \"${tarball_input}\" == *$'\\r'* || \"${tarball_input}\" == *$'\\t'*", verify_dist)
        self.assertIn("contains_control_chars() {", verify_snap)
        self.assertNotIn("archive path contains control characters: %s", verify_dist)
        self.assertNotIn("snap file path contains control characters: %s", verify_snap)
        self.assertNotIn("${snap_path}\" == *$'\\n'* || \"${snap_path}\" == *$'\\r'* || \"${snap_path}\" == *$'\\t'*", verify_snap)
        self.assertIn('safe_fs rmdir uninstall "${app_data}" --ignore-non-empty', uninstall_local)
        self.assertNotIn('safe_fs rmdir uninstall "${app_data}" --ignore-non-empty || true', uninstall_local)

        safe_fs = (REPO_ROOT / "scripts" / "safe-local-fs.py").read_text(encoding="utf-8")
        self.assertIn("COPY_CHUNK_SIZE = 1 << 20", safe_fs)
        self.assertIn("source file must not be hardlinked during", safe_fs)
        self.assertIn("_copy_file_atomically_from_checked_source", safe_fs)
        self.assertIn("def _rmtree_safe(", safe_fs)
        self.assertIn("shutil.rmtree is not fd-safe", safe_fs)
        self.assertIn('getattr(shutil.rmtree, "avoids_symlink_attacks", False)', safe_fs)
        self.assertNotIn("data = handle.read()", safe_fs)

    def test_publish_script_stages_all_assets_before_release_upload(self) -> None:
        publish_script = (REPO_ROOT / "scripts" / "publish-github-release.sh").read_text(encoding="utf-8")

        self.assertIn("upload_refs=()", publish_script)
        self.assertIn('for asset in "${assets[@]}"; do', publish_script)
        self.assertIn('staged_path="${staging_dir}/${staged_name}"', publish_script)
        self.assertIn('if [[ "${asset}" == "${generic_asset}" ]]; then', publish_script)
        self.assertIn('staged_name="$(generic_asset_label "${asset}")"', publish_script)
        self.assertIn('if [[ -n "${staged_names_seen[${staged_name}]:-}" ]]; then', publish_script)
        self.assertIn('asset_abs="$(realpath "${asset}")"', publish_script)
        self.assertIn('copy-file publish "${asset_abs}" "${staged_path}" 0644', publish_script)
        self.assertIn('verify_asset_path "${staged_path}"', publish_script)
        self.assertNotIn("chmod 0444 -- \"${staged_path}\"", publish_script)
        self.assertIn("chmod_and_fsync_regular_file() {", publish_script)
        self.assertIn('chmod_and_fsync_regular_file "${staged_path}" 0444 "staged release asset"', publish_script)
        self.assertIn('gh release upload "${tag}" "${upload_refs[@]}" --repo "${repo}"', publish_script)
        self.assertIn('if ! gh release upload "${tag}" "${upload_refs[@]}" --repo "${repo}"; then', publish_script)
        self.assertIn('if ! gh release edit "${tag}" \\', publish_script)
        self.assertIn("failed to publish release after uploading assets.", publish_script)
        self.assertIn('--draft', publish_script)
        self.assertIn("--draft=false", publish_script)
        self.assertIn("trap cleanup_notes EXIT", publish_script)

    def test_publish_script_rejects_only_strict_semver_tag(self) -> None:
        publish_script = (REPO_ROOT / "scripts" / "publish-github-release.sh").read_text(encoding="utf-8")

        self.assertIn('if [[ ! "${tag}" =~ ^v[0-9]+\\.[0-9]+\\.[0-9]+$ ]]; then', publish_script)
        self.assertNotIn("^(\\.[0-9]+){0,2}([0-9A-Za-z.+-]*)?$", publish_script)

    def test_publish_script_resolves_repository_from_verified_checkout(self) -> None:
        publish_script = (REPO_ROOT / "scripts" / "publish-github-release.sh").read_text(encoding="utf-8")
        self.assertIn("resolve_github_remote_repo()", publish_script)
        self.assertIn("git remote get-url origin", publish_script)
        self.assertIn('remote_url="${remote_url%.git}"', publish_script)
        self.assertIn("GITHUB_REPOSITORY is not set and origin is not a GitHub repository; cannot verify target repository safely.", publish_script)
        self.assertIn("checked out origin (%s) does not match GITHUB_REPOSITORY (%s).", publish_script)
        self.assertIn('readonly RELEASE_TARGET_REPOSITORY="H234598/speed-of-cinnamon"', publish_script)
        self.assertNotIn('repo="${GITHUB_REPOSITORY:-H234598/speed-of-cinnamon}"', publish_script)

    def test_wiki_publish_does_not_bootstrap_after_clone_failure(self) -> None:
        publish_script = (REPO_ROOT / "scripts" / "publish-wiki.sh").read_text(encoding="utf-8")
        self.assertIn("failed to clone wiki repository", publish_script)
        self.assertNotIn("git -C \"${work_dir}/wiki\" init", publish_script)

    def test_frogbot_checkouts_do_not_persist_credentials(self) -> None:
        for workflow_name in ("frogbot-scan-and-fix.yml", "frogbot-scan-pr.yml"):
            workflow = (REPO_ROOT / ".github" / "workflows" / workflow_name).read_text(encoding="utf-8")
            self.assertIn("uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6", workflow)
            self.assertIn("persist-credentials: false", workflow)
            self.assertIn("JF_GIT_TOKEN: ${{ github.token }}", workflow)
            self.assertNotIn("secrets.GITHUB_TOKEN", workflow)

    def test_frogbot_scan_and_fix_limits_top_level_permissions(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "frogbot-scan-and-fix.yml").read_text(encoding="utf-8")
        top_level_permissions = _workflow_block_lines(workflow, "permissions:")

        self.assertEqual(top_level_permissions, ["  contents: read"])
        self.assertIn(
            "  create-fix-pull-requests:\n"
            "    needs: check-frogbot-secrets\n"
            "    runs-on: ubuntu-24.04\n"
            "    if: needs.check-frogbot-secrets.outputs.configured == 'true'\n"
            "    permissions:\n"
            "      contents: write\n"
            "      pull-requests: write\n"
            "      security-events: write",
            workflow,
        )

    def test_frogbot_pr_scan_limits_top_level_permissions(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "frogbot-scan-pr.yml").read_text(encoding="utf-8")
        top_level_permissions = _workflow_block_lines(workflow, "permissions:")

        self.assertEqual(top_level_permissions, ["  contents: read"])
        self.assertIn(
            "  scan-pull-request:\n"
            "    needs: check-frogbot-secrets\n"
            "    runs-on: ubuntu-24.04\n"
            "    if: needs.check-frogbot-secrets.outputs.configured == 'true'\n"
            "    # A pull request needs to be approved, before Frogbot scans it.",
            workflow,
        )
        self.assertIn(
            "    permissions:\n"
            "      contents: read\n"
            "      pull-requests: write",
            workflow,
        )

    def test_frogbot_pr_scan_does_not_use_pull_request_target(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "frogbot-scan-pr.yml").read_text(encoding="utf-8")
        self.assertIn("pull_request:", workflow)
        self.assertNotIn("pull_request_target:", workflow)
        self.assertIn("ref: ${{ github.event.pull_request.head.sha }}", workflow)

    def test_authorship_guard_is_part_of_check_target(self) -> None:
        makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        verifier = (REPO_ROOT / "scripts" / "verify-authorship.sh").read_text(encoding="utf-8")

        self.assertIn("check: test lint lint-workflows-check verify-authorship smoke-doctor security-scan", makefile)
        self.assertIn("coverage:", makefile)
        self.assertIn("coverage run --source=src/speed_of_cinnamon", makefile)
        self.assertIn("coverage lcov -o reports/lcov.info", makefile)
        self.assertIn("PYTHON := $(shell command -v python3", makefile)
        self.assertIn("ifneq ($(strip $(PYTHON)),)", makefile)
        self.assertIn("override PYTHON := $(PYTHON)", makefile)
        self.assertIn("$(error python3 is required)", makefile)
        self.assertIn("verify-authorship:\n\t./scripts/verify-authorship.sh", makefile)
        self.assertIn("check: test lint lint-workflows-check verify-authorship smoke-doctor security-scan", makefile)
        self.assertIn("python-security-scan:\n\tbandit -q -r src/speed_of_cinnamon scripts -x tests", makefile)
        self.assertIn("shell-security-scan:\n\tshellcheck scripts/*.sh", makefile)
        self.assertIn("security-scan: python-security-scan shell-security-scan", makefile)
        self.assertIn('expected_name = "H234598"', verifier)
        self.assertIn('expected_email = "54270221+H234598@users.noreply.github.com"', verifier)
        self.assertIn('expected_repo = "github.com/H234598/speed-of-cinnamon"', verifier)
        self.assertIn("allowed_remote_urls = {", verifier)
        self.assertIn("normalize_remote_url(remote)", verifier)
        self.assertIn('remote_stdout = run_git("config", "--get", "remote.origin.url", check=False).stdout', verifier)
        self.assertIn('remote = remote_stdout.removesuffix("\\n")', verifier)
        self.assertIn("if remote != remote.strip():", verifier)
        self.assertNotIn('run_git("config", "--get", "remote.origin.url", check=False).stdout.strip()', verifier)
        self.assertIn("0x80 <= ord(char) <= 0x9F for char in remote", verifier)
        self.assertIn("normalized_remote not in allowed_remote_urls", verifier)
        self.assertNotIn("expected_repo not in normalized_remote", verifier)
        self.assertIn('allowed_committers = {', verifier)
        self.assertIn('("GitHub", "noreply@github.com")', verifier)
        self.assertIn("check_forbidden_names()", verifier)
        self.assertIn("check_git_identity()", verifier)
        self.assertNotIn("check_mailmap", verifier)

    def test_makefile_release_validation_gate(self) -> None:
        makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn("release-validate-flags", makefile)
        self.assertRegex(
            makefile,
            r"(?m)^dist-check:\s*release-validate-flags$",
            "dist-check must validate release flags before running",
        )
        self.assertRegex(makefile, r"(?m)^rpm:\s*release-validate-flags$", "rpm must validate release flags before running")
        self.assertRegex(
            makefile, r"(?m)^rpm-check:\s*release-validate-flags$", "rpm-check must validate release flags before running"
        )
        self.assertRegex(
            makefile, r"(?m)^rpm-generic:\s*release-validate-flags$", "rpm-generic must validate release flags before running"
        )
        self.assertRegex(
            makefile,
            r"(?m)^rpm-generic-check:\s*release-validate-flags$",
            "rpm-generic-check must validate release flags before running",
        )

    def test_makefile_lint_workflows_check_in_check_target(self) -> None:
        makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        lint_workflows = (REPO_ROOT / "scripts" / "lint-workflows.sh").read_text(encoding="utf-8")
        self.assertIn("lint-workflows-check:", makefile)
        self.assertIn("lint-workflows-check", makefile)

        self.assertIn("\t@if [ \"$${GITHUB_ACTIONS:-false}\" = \"true\" ]; then \\", makefile)
        self.assertIn("\t  if [ \"$${GITHUB_ACTIONS:-false}\" = \"true\" ]; then \\", makefile)
        self.assertIn("./scripts/lint-workflows.sh \\", makefile)
        self.assertIn("workflow lint skipped locally; install actionlint for strict checks.", makefile)
        self.assertIn("shopt -s nullglob", lint_workflows)
        self.assertIn('workflows=(.github/workflows/*.yml .github/workflows/*.yaml)', lint_workflows)
        self.assertIn('if [[ "${#workflows[@]}" -eq 0 ]]; then', lint_workflows)

    def test_man_pages_and_wiki_are_packaged(self) -> None:
        spec = (REPO_ROOT / "packaging" / "speed-of-cinnamon.spec").read_text(encoding="utf-8")
        generic_spec = (REPO_ROOT / "packaging" / "speed-of-cinnamon-generic.spec").read_text(encoding="utf-8")
        dist_verifier = (REPO_ROOT / "scripts" / "verify-dist.sh").read_text(encoding="utf-8")
        rpm_verifier = (REPO_ROOT / "scripts" / "verify-rpm.sh").read_text(encoding="utf-8")
        install_local = (REPO_ROOT / "scripts" / "install-local.sh").read_text(encoding="utf-8")
        wiki_publisher = (REPO_ROOT / "scripts" / "publish-wiki.sh").read_text(encoding="utf-8")

        for path in [
            "docs/man/speed-of-cinnamon.1",
            "docs/man/speed-of-cinnamon-alarms.1",
            "docs/wiki/Home.md",
        ]:
            self.assertTrue((REPO_ROOT / path).exists(), f"{path} should exist")
            self.assertIn(path, dist_verifier)

        self.assertIn("install -m 0644 docs/man/speed-of-cinnamon.1", spec)
        self.assertIn("install -m 0644 docs/man/speed-of-cinnamon-alarms.1", spec)
        for rpm_spec in (spec, generic_spec):
            self.assertIn('find src/speed_of_cinnamon \\( -type l -o -type f -links +1 \\) -print -quit', rpm_spec)
            self.assertIn('find files/speed-of-cinnamon@H234598 \\( -type l -o -type f -links +1 \\) -print -quit', rpm_spec)
            self.assertIn("refusing unsafe python package source tree", rpm_spec)
            self.assertIn("refusing unsafe applet source tree", rpm_spec)
            self.assertIn("refusing python package source tree with control characters in file names", rpm_spec)
            self.assertIn("refusing applet source tree with control characters in file names", rpm_spec)
        self.assertIn("%{_mandir}/man1/speed-of-cinnamon.1*", spec)
        self.assertIn("%{_mandir}/man1/speed-of-cinnamon-alarms.1*", spec)
        self.assertIn("speed-of-cinnamon\\.1(\\.gz)?", rpm_verifier)
        self.assertIn("speed-of-cinnamon-alarms\\.1(\\.gz)?", rpm_verifier)
        self.assertIn("docs/man/speed-of-cinnamon.1", install_local)
        self.assertIn('SPEED_OF_CINNAMON_TEST_HOME:-0', install_local)
        self.assertIn('readonly REQUIRED_TOOLS=(dirname find grep getent id mktemp realpath cut python3)', install_local)
        self.assertIn("check_required_tools()", install_local)
        self.assertIn("missing_tool", install_local)
        self.assertIn("required tool missing:", install_local)
        self.assertIn('repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"', install_local)
        self.assertNotIn("for tool in find grep command realpath; do", install_local)
        self.assertIn('dbus_send_command=""', install_local)
        self.assertIn(
            'if [[ -n "${DBUS_SESSION_BUS_ADDRESS:-}" && -n "${account_home}" && "${HOME}" == "${account_home}" ]]; then',
            install_local,
        )
        self.assertIn('dbus_send_command="$(command -v -- dbus-send || true)"', install_local)
        self.assertIn('if [[ -n "${dbus_send_command}" ]]; then', install_local)
        self.assertIn("reject_unsafe_tree()", install_local)
        self.assertIn('find "${tree}" \\( -type l -o -type f -links +1 \\) -print -quit', install_local)
        self.assertIn("reject_unsafe_file()", install_local)
        self.assertIn("write_staging_dir()", install_local)
        self.assertIn("activate_staged()", install_local)
        self.assertIn("rollback_staged_items()", install_local)
        self.assertIn("validate_staged_workspace()", install_local)
        self.assertIn('staged_workspace="$(mktemp -d "${app_data}/install-stage-XXXXXX")"', install_local)
        self.assertNotIn('staged_workspace="$(mktemp -d "${temp_root}/speed-of-cinnamon-install-stage-XXXXXX")"', install_local)
        self.assertIn("resolve_tmp_root >/dev/null", install_local)
        self.assertIn('safe_fs remove install "${staged_workspace}" --kind dir', install_local)
        self.assertNotIn('rm -rf -- "${staged_workspace}"', install_local)
        self.assertIn('if [[ -L "${staged_workspace}" || ! -d "${staged_workspace}" ]]; then', install_local)
        self.assertIn('if [[ "${staged_real}" != "${app_data_real}/install-stage-"* ]]; then', install_local)
        self.assertIn(
            'safe_fs write-wrapper install "${stage_root}/speed-of-cinnamon/bin/speed-of-cinnamon" "${app_data}/python" "${python3_path}"',
            install_local,
        )
        self.assertIn('readonly TRUSTED_COMMAND_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"', install_local)
        self.assertIn('export PATH="${TRUSTED_COMMAND_PATH}"', install_local)
        self.assertIn('readonly TRUSTED_COMMAND_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"', wiki_publisher)
        self.assertIn('export PATH="${TRUSTED_COMMAND_PATH}"', wiki_publisher)
        self.assertIn('safe_fs="${repo_dir}/scripts/safe-local-fs.py"', wiki_publisher)
        self.assertIn('safe_fs_cmd=(python3 "${safe_fs}")', wiki_publisher)
        self.assertIn('expected_wiki_url="https://github.com/H234598/speed-of-cinnamon.wiki.git"', wiki_publisher)
        self.assertIn('wiki_url="${WIKI_URL:-${expected_wiki_url}}"', wiki_publisher)
        self.assertIn('if [[ "${wiki_url}" != "${expected_wiki_url}" ]]; then', wiki_publisher)
        self.assertIn('Invalid wiki URL: expected %s, got %s', wiki_publisher)
        self.assertIn('temporary root must be an absolute path:', wiki_publisher)
        self.assertIn('temporary root must not be a symlink:', wiki_publisher)
        self.assertIn('temporary root is not a writable directory:', wiki_publisher)
        self.assertIn('work_dir_abs="$(realpath "${work_dir}")', wiki_publisher)
        self.assertIn('temporary wiki publish workspace escaped temporary root', wiki_publisher)
        self.assertIn('"${safe_fs_cmd[@]}" mkdirs publish-wiki "${work_root}"', wiki_publisher)
        self.assertIn('"${safe_fs_cmd[@]}" remove publish-wiki "${work_dir}" --kind dir', wiki_publisher)
        self.assertIn('"${safe_fs_cmd[@]}" copy-file publish-wiki "${repo_dir}/docs/wiki/Home.md" "${work_dir}/wiki/Home.md" 0644', wiki_publisher)
        self.assertIn('"${safe_fs_cmd[@]}" copy-file publish-wiki "${repo_dir}/docs/user-guide.md" "${work_dir}/wiki/User-Guide.md" 0644', wiki_publisher)
        self.assertNotIn('cp "${repo_dir}/docs/wiki/Home.md"', wiki_publisher)
        self.assertNotIn('rm -rf -- "${work_dir}"', wiki_publisher)
        self.assertIn('safe_fs install-tree install "${source_root}/src/speed_of_cinnamon" "${stage_root}/speed-of-cinnamon/python/speed_of_cinnamon" "python package"', install_local)
        self.assertIn('activated_had_existing+=("0")', install_local)
        self.assertIn('remove install "${target}" --kind "${kind}"', install_local)
        self.assertNotIn('mktemp -d "${parent}/.${name}.install.', install_local)
        self.assertIn('safe_fs copy-file install "${source_root}/docs/man/speed-of-cinnamon.1"', install_local)
        self.assertIn("python3 -m compileall -q", dist_verifier)
        self.assertIn('${package_dir}/scripts/safe-local-fs.py', dist_verifier)
        self.assertIn("archive backend wrapper helper does not invoke the expected CLI module", dist_verifier)
        self.assertIn("scripts/safe-local-fs.py", dist_verifier)
        self.assertIn("archive backend wrapper helper must not resolve python3 through PATH at runtime", dist_verifier)
        self.assertIn('python_executable = _validate_absolute(args.python_executable, "python executable path")', dist_verifier)
        self.assertIn('write_wrapper.add_argument("python_executable")', dist_verifier)
        self.assertIn(' -m speed_of_cinnamon.cli \\"$@\\"', dist_verifier)
        self.assertNotIn('exec "$(command -v -- python3)" -m speed_of_cinnamon.cli "$@"', dist_verifier)
        self.assertIn('safe_fs="${repo_dir}/scripts/safe-local-fs.py"', dist_verifier)
        self.assertIn('safe_fs_cmd=(python3 "${safe_fs}")', dist_verifier)
        self.assertIn('"${safe_fs_cmd[@]}" copy-file verify-dist "${tarball}" "${tarball_snapshot}" 0644', dist_verifier)
        self.assertIn('"${safe_fs_cmd[@]}" remove verify-dist "${tmp_dir}" --kind dir', dist_verifier)
        self.assertIn('tarball_snapshot="${tmp_dir}/speed-of-cinnamon-verify.tar.gz"', dist_verifier)
        self.assertIn("RPM package contains unsafe path entry", rpm_verifier)
        self.assertIn("RPM expansion contains unsupported symlink entries.", rpm_verifier)
        self.assertIn("RPM expansion contains unsupported hardlink entries.", rpm_verifier)
        self.assertIn("RPM package must be a regular file", rpm_verifier)
        self.assertIn('safe_fs="${repo_dir}/scripts/safe-local-fs.py"', rpm_verifier)
        self.assertIn('"${safe_fs_cmd[@]}" copy-file verify-rpm "${rpm_path}" "${rpm_snapshot}" 0644', rpm_verifier)
        self.assertIn('"${safe_fs_cmd[@]}" remove verify-rpm "${tmp_dir}" --kind dir', rpm_verifier)
        self.assertIn('rpm_snapshot="${tmp_dir}/speed-of-cinnamon-verify.rpm"', rpm_verifier)
        self.assertIn('rpm -qp --qf', rpm_verifier)
        self.assertIn('"${rpm_snapshot}" > "${metadata_file}"', rpm_verifier)
        self.assertIn('rpm -qpl "${rpm_snapshot}" > "${file_list}"', rpm_verifier)
        self.assertIn('file_metadata="${tmp_dir}/rpm-file-metadata.txt"', rpm_verifier)
        self.assertIn("%{FILEMODES:octal}", rpm_verifier)
        self.assertIn("%{FILELINKTOS}", rpm_verifier)
        self.assertIn("RPM package contains unsupported file type", rpm_verifier)
        self.assertIn("RPM package contains unsupported link target", rpm_verifier)
        self.assertIn("RPM package file metadata does not match file listing", rpm_verifier)
        self.assertIn('rpm2cpio "${rpm_snapshot}" | cpio -idmu --no-absolute-filenames --quiet', rpm_verifier)
        self.assertIn("python3 -m compileall -q", rpm_verifier)
        build_rpm = (REPO_ROOT / "scripts" / "build-rpm.sh").read_text(encoding="utf-8")
        self.assertIn('py_auto_byte_compile 0', build_rpm)
        self.assertIn('__brp_python_bytecompile %{nil}', build_rpm)
        self.assertIn('__brp_python_hardlink %{nil}', build_rpm)
        self.assertIn("speed-of-cinnamon.wiki.git", wiki_publisher)
        self.assertIn("User-Guide.md", wiki_publisher)

    def test_verify_dist_blocks_dangerous_archive_entries(self) -> None:
        dist_verifier = (REPO_ROOT / "scripts" / "verify-dist.sh").read_text(encoding="utf-8")
        self.assertIn("tarfile.open(tarball_snapshot, \"r:gz\")", dist_verifier)
        self.assertIn("member.issym()", dist_verifier)
        self.assertIn("member.islnk()", dist_verifier)
        self.assertIn("raise SystemExit(f\"dist archive contains unsupported link entry", dist_verifier)
        self.assertIn("package_root = None", dist_verifier)
        self.assertIn("dist archive contains multiple top-level entries", dist_verifier)
        self.assertIn("dist archive contains an empty path entry", dist_verifier)
        self.assertIn("source = archive.extractfile(member)", dist_verifier)
        self.assertIn("os.O_WRONLY | os.O_CREAT | os.O_EXCL", dist_verifier)
        self.assertIn("dist archive contains duplicate file entry", dist_verifier)
        self.assertNotIn("archive.extract(member, target)", dist_verifier)

    def test_verify_dist_uses_private_snapshot_for_tar_tooling(self) -> None:
        verify_dist = (REPO_ROOT / "scripts" / "verify-dist.sh").read_text(encoding="utf-8")
        snapshot_copy = verify_dist.index('"${safe_fs_cmd[@]}" copy-file verify-dist "${tarball}" "${tarball_snapshot}" 0644')
        tar_listing = verify_dist.index('tar -tzf "${tarball_snapshot}"')
        tarfile_open = verify_dist.index('tarfile.open(tarball_snapshot, "r:gz")')

        self.assertLess(snapshot_copy, tar_listing)
        self.assertLess(snapshot_copy, tarfile_open)
        self.assertIn("archive must not be hardlinked", verify_dist)
        self.assertIn("archive snapshot must not be hardlinked", verify_dist)
        self.assertIn('tarball_input="$1"', verify_dist)
        self.assertIn("archive path contains control characters", verify_dist)
        self.assertIn("archive must not be a symlink", verify_dist)
        self.assertIn("archive missing or invalid", verify_dist)
        self.assertIn("failed to resolve archive path", verify_dist)
        self.assertIn('realpath "${tarball_input}" 2>/dev/null', verify_dist)
        self.assertNotIn('tar -tzf "${tarball}"', verify_dist)
        self.assertNotIn('tarfile.open(tarball, "r:gz")', verify_dist)

    def test_dev_backend_path_does_not_append_env_pythonpath(self) -> None:
        dev_backend = (REPO_ROOT / "scripts" / "dev-backend.sh").read_text(encoding="utf-8")
        self.assertNotIn("PYTHONPATH:+", dev_backend)
        self.assertIn('export PYTHONPATH="${repo_dir}/src"', dev_backend)

    def test_build_snap_rejects_symlinked_snap_dir(self) -> None:
        build_snap = (REPO_ROOT / "scripts" / "build-snap.sh").read_text(encoding="utf-8")
        verify_snap = (REPO_ROOT / "scripts" / "verify-snap.sh").read_text(encoding="utf-8")
        self.assertIn('snap_dir="${repo_dir}/snap"', build_snap)
        self.assertIn('for tool in python3 snapcraft mktemp mkdir find realpath stat chmod grep sort basename; do', build_snap)
        self.assertIn('if [[ -L "${snap_dir}" ]]; then', build_snap)
        self.assertIn('snap directory must not be a symlink', build_snap)
        self.assertIn('dist_parent="${repo_dir}/dist"', build_snap)
        self.assertIn('if [[ -L "${dist_parent}" ]]; then', build_snap)
        self.assertIn('dist directory must not be a symlink', build_snap)
        self.assertIn('dist_dir="${dist_parent}/snap"', build_snap)
        self.assertIn('snapcraft_file="${snap_dir}/snapcraft.yaml"', build_snap)
        self.assertIn('safe_fs="${repo_dir}/scripts/safe-local-fs.py"', build_snap)
        self.assertIn('require_regular_source_file "${safe_fs}" "safe local filesystem helper"', build_snap)
        self.assertIn('safe_fs_cmd=(python3 "${safe_fs}")', build_snap)
        self.assertIn('repo_tmp_abs="$(realpath "${repo_tmp_root}")"', build_snap)
        self.assertIn('repo_tmp_root="${repo_tmp_abs}"', build_snap)
        self.assertIn('snap temporary root must be outside repository', build_snap)
        self.assertIn('snap_workspace="$(mktemp -d "${repo_tmp_root}/speed-of-cinnamon-snap-tree-XXXXXX")"', build_snap)
        self.assertIn('snap_workspace_abs="$(realpath "${snap_workspace}")', build_snap)
        self.assertIn('temporary snap workspace escaped temporary root', build_snap)
        self.assertIn('snapcraft_file_rendered="${snap_workspace}/snap/snapcraft.yaml"', build_snap)
        self.assertIn('install-tree build-snap "${repo_dir}/snap" "${snap_workspace}/snap" "snap source tree"', build_snap)
        self.assertIn('install-tree build-snap "${repo_dir}/src" "${snap_workspace}/src" "Python source tree"', build_snap)
        self.assertIn('copy-file build-snap "${repo_dir}/pyproject.toml" "${snap_workspace}/pyproject.toml" 0644', build_snap)
        self.assertIn('copy-file build-snap "${repo_dir}/README.md" "${snap_workspace}/README.md" 0644', build_snap)
        self.assertNotIn('install-tree build-snap "${repo_dir}" "${snap_workspace}" "snap temporary source tree"', build_snap)
        self.assertIn('mkdir -p "${snap_workspace_dist}"', build_snap)
        self.assertIn('python3 - "${snapcraft_file_rendered}" "${snapcraft_file_rendered}" "${version}" "${snapcraft_base}"', build_snap)
        self.assertNotIn('output_path.write_text', build_snap)
        self.assertIn('os.replace(tmp_name, output_path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)', build_snap)
        self.assertIn('os.fsync(parent_fd)', build_snap)
        self.assertIn("cleanup_existing_dist_snaps() {", build_snap)
        self.assertIn('find "${dist_dir}" -maxdepth 1 -name "speed-of-cinnamon_*.snap" ! -type f -print -quit', build_snap)
        self.assertIn('"${safe_fs_cmd[@]}" remove build-snap "${existing_snap}" --kind file', build_snap)
        self.assertIn("cleanup_existing_root_snaps() {", build_snap)
        self.assertIn(
            'find "${repo_dir}" -maxdepth 1 -name "speed-of-cinnamon_${version}_*.snap" ! -type f -print -quit',
            build_snap,
        )
        self.assertIn('"${safe_fs_cmd[@]}" remove build-snap "${existing_root_snap}" --kind file', build_snap)
        self.assertIn('"${safe_fs_cmd[@]}" copy-file build-snap "${snap_files[0]}" "${output_path}" 0644 --dst-must-not-exist', build_snap)
        self.assertNotIn('rm -rf -- "${snap_workspace}"', build_snap)
        self.assertNotIn('snapcraft_backup', build_snap)
        self.assertNotIn('mv -f -- "${snapcraft_backup}" "${snapcraft_file}"', build_snap)
        self.assertNotIn("mv -T", build_snap)
        self.assertNotIn("NamedTemporaryFile", build_snap)
        self.assertIn("cleanup_existing_root_snaps() {", build_snap)
        self.assertIn("speed-of-cinnamon-snap-root-cleanup-XXXXXX", build_snap)
        self.assertIn("refusing to clean non-regular snap artifact from repository root", build_snap)
        self.assertNotIn('refusing to overwrite existing snap artifact for version', build_snap)
        self.assertNotIn('if find "${repo_dir}" -maxdepth 1 -name "speed-of-cinnamon_${version}_*.snap" -print -quit | grep -q .; then', build_snap)
        self.assertNotIn('rm -f -- "${dist_dir}/speed-of-cinnamon_${version}"_*.snap', build_snap)
        self.assertNotIn('path.with_name(path.name + ".tmp")', build_snap)
        self.assertIn('snap_dir="${repo_dir}/dist/snap"', verify_snap)
        self.assertIn("require_cmd grep", verify_snap)
        self.assertIn("require_cmd basename", verify_snap)
        self.assertIn('if [[ -L "${snap_dir}" ]]; then', verify_snap)
        self.assertIn('snap directory must not be a symlink', verify_snap)
        self.assertIn('repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"', verify_snap)
        self.assertIn('safe_fs="${repo_dir}/scripts/safe-local-fs.py"', verify_snap)
        self.assertIn('tmp_dir_abs="$(realpath "${tmp_dir}")', verify_snap)
        self.assertIn('if contains_control_chars "${tmp_root}"; then', verify_snap)
        self.assertIn('temporary root contains control characters', verify_snap)
        self.assertIn('realpath "${snap_path}" 2>/dev/null', verify_snap)
        self.assertIn('realpath "${tmp_root}" 2>/dev/null', verify_snap)
        self.assertNotIn('temporary root must be an absolute path: %s', verify_snap)
        self.assertNotIn('temporary root must not be a symlink: %s', verify_snap)
        self.assertNotIn('temporary root is not a writable directory: %s', verify_snap)
        self.assertNotIn('failed to resolve temporary root: %s', verify_snap)
        self.assertIn('temporary snap verification directory escaped temporary root', verify_snap)
        self.assertIn('"${safe_fs_cmd[@]}" copy-file verify-snap "${absolute}" "${snap_snapshot}" 0644', verify_snap)
        self.assertIn('"${safe_fs_cmd[@]}" remove verify-snap "${tmp_dir}" --kind dir', verify_snap)
        self.assertIn('unsquashfs -lln -no-progress "${snap_snapshot}" > "${snap_listing}"', verify_snap)
        self.assertIn("snap package contains unsupported entry type", verify_snap)
        self.assertIn("snap package contains unsupported link entry", verify_snap)
        self.assertIn("snap package contains malformed link entry", verify_snap)
        self.assertIn("snap package contains unsafe link target", verify_snap)
        self.assertIn("def validate_symlink_target(path: PurePosixPath, target_text: str) -> None:", verify_snap)
        self.assertIn("resolved = posixpath.normpath(posixpath.join(str(path.parent), target_text))", verify_snap)
        self.assertIn("snap package is missing required entries", verify_snap)
        self.assertIn("squashfs-root/meta/snap.yaml", verify_snap)
        self.assertIn("squashfs-root/bin/speed-of-cinnamon", verify_snap)
        self.assertIn("squashfs-root/src/speed_of_cinnamon/cli.py", verify_snap)
        self.assertIn("REQUIRED_REGULAR_ENTRIES = {", verify_snap)
        self.assertIn("for required_entry in REQUIRED_REGULAR_ENTRIES:", verify_snap)
        self.assertIn('required entry is not regular file', verify_snap)
        self.assertLess(
            verify_snap.index("for required_entry in REQUIRED_REGULAR_ENTRIES:"),
            verify_snap.index("unsquashfs -cat"),
        )
        self.assertIn('unsquashfs -cat "${snap_snapshot}" meta/snap.yaml > "${snap_yaml}"', verify_snap)
        self.assertIn('unsquashfs -cat "${snap_snapshot}" bin/speed-of-cinnamon > "${snap_backend}"', verify_snap)
        self.assertIn("src/speed_of_cinnamon/cli.py", verify_snap)
        self.assertIn("snap file must not be hardlinked", verify_snap)
        self.assertIn("contains_control_chars() {", verify_snap)
        self.assertIn("0x80 <= ord(char) <= 0x9F for char in value", verify_snap)
        self.assertIn("0xDC80 <= ord(char) <= 0xDCFF for char in value", verify_snap)
        self.assertIn('snap file path contains control characters', verify_snap)
        self.assertNotIn('snap file path contains control characters: %s', verify_snap)

    def test_build_snap_and_verify_rpm_guard_paths_against_canonical_repo_dir(self) -> None:
        build_snap = (REPO_ROOT / "scripts" / "build-snap.sh").read_text(encoding="utf-8")
        verify_rpm = (REPO_ROOT / "scripts" / "verify-rpm.sh").read_text(encoding="utf-8")

        self.assertIn('repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"', build_snap)
        self.assertIn('repo_tmp_abs="$(realpath "${repo_tmp_root}")"', build_snap)
        self.assertIn('if [[ "${repo_tmp_abs}" == "${repo_dir}" || "${repo_tmp_abs}" == "${repo_dir}/"* ]]; then', build_snap)
        self.assertIn('repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"', verify_rpm)
        self.assertIn("contains_control_chars() {", verify_rpm)
        self.assertIn("0x80 <= ord(char) <= 0x9F for char in value", verify_rpm)
        self.assertIn("0xDC80 <= ord(char) <= 0xDCFF for char in value", verify_rpm)
        self.assertIn('if contains_control_chars "${rpm_path}"; then', verify_rpm)
        self.assertIn("RPM package path contains control characters", verify_rpm)
        self.assertIn('if ! rpm_path="$(realpath "${rpm_path}" 2>/dev/null)"; then', verify_rpm)
        self.assertIn('if [[ -L "${rpm_path}" || ! -f "${rpm_path}" || ! ( "${rpm_path}" == "${repo_dir}/dist/rpmbuild/"*".rpm" ||', verify_rpm)

    def test_verify_rpm_uses_private_snapshot_for_rpm_tooling(self) -> None:
        verify_rpm = (REPO_ROOT / "scripts" / "verify-rpm.sh").read_text(encoding="utf-8")
        snapshot_copy = verify_rpm.index('"${safe_fs_cmd[@]}" copy-file verify-rpm "${rpm_path}" "${rpm_snapshot}" 0644')
        metadata_check = verify_rpm.index('"${rpm_snapshot}" > "${metadata_file}"')
        file_list_check = verify_rpm.index('rpm -qpl "${rpm_snapshot}" > "${file_list}"')
        file_metadata_check = verify_rpm.index('"${rpm_snapshot}" > "${file_metadata}"')
        extraction_check = verify_rpm.index('rpm2cpio "${rpm_snapshot}" | cpio -idmu --no-absolute-filenames --quiet')

        self.assertLess(snapshot_copy, metadata_check)
        self.assertLess(snapshot_copy, file_list_check)
        self.assertLess(snapshot_copy, file_metadata_check)
        self.assertLess(file_metadata_check, extraction_check)
        self.assertLess(snapshot_copy, extraction_check)
        self.assertNotIn('rpm -qp --qf \'name=%{NAME}\\nversion=%{VERSION}\\narch=%{ARCH}\\npackager=%{PACKAGER}\\nvendor=%{VENDOR}\\nurl=%{URL}\\n\' "${rpm_path}"', verify_rpm)
        self.assertNotIn('rpm -qpl "${rpm_path}"', verify_rpm)
        self.assertNotIn('rpm2cpio "${rpm_path}"', verify_rpm)

    def test_temp_root_resolution_is_fail_closed_in_build_dist_and_install_local(self) -> None:
        build_dist = (REPO_ROOT / "scripts" / "build-dist.sh").read_text(encoding="utf-8")
        install_local = (REPO_ROOT / "scripts" / "install-local.sh").read_text(encoding="utf-8")

        self.assertIn('work_root="${TMPDIR:-/tmp}"', build_dist)
        self.assertIn('printf \'temporary root must be an absolute path:', build_dist)
        self.assertIn('temporary root must be an absolute path:', build_dist)
        self.assertIn('printf \'temporary root must not be a symlink:', build_dist)
        self.assertIn('printf \'failed to resolve temporary root:', build_dist)
        self.assertNotIn("${repo_dir}/.tmp", build_dist)

        self.assertIn('local base="${TMPDIR:-/tmp}"', install_local)
        self.assertIn('temporary root must be an absolute path:', install_local)
        self.assertIn('printf \'temporary root must not be a symlink:', install_local)
        self.assertIn('printf \'failed to resolve temporary root:', install_local)
        self.assertNotIn("${repo_dir}/.tmp", install_local)

    def test_tmp_root_resolves_fail_closed_in_build_and_verify_rpm_and_dist(self) -> None:
        build_rpm = (REPO_ROOT / "scripts" / "build-rpm.sh").read_text(encoding="utf-8")
        verify_rpm = (REPO_ROOT / "scripts" / "verify-rpm.sh").read_text(encoding="utf-8")
        verify_dist = (REPO_ROOT / "scripts" / "verify-dist.sh").read_text(encoding="utf-8")

        self.assertIn('repo_tmp_root="${TMPDIR:-/tmp}"', build_rpm)
        self.assertIn('temporary root must be an absolute path:', build_rpm)
        self.assertIn('temporary root must not be a symlink:', build_rpm)
        self.assertIn('temporary root is not a writable directory:', build_rpm)
        self.assertIn('failed to resolve temporary root:', build_rpm)
        self.assertNotIn("${repo_dir}/.tmp", build_rpm)

        self.assertIn('tmp_root="${TMPDIR:-/tmp}"', verify_rpm)
        self.assertIn('if contains_control_chars "${tmp_root}"; then', verify_rpm)
        self.assertIn('temporary root contains control characters', verify_rpm)
        self.assertIn('temporary root must be an absolute path', verify_rpm)
        self.assertIn('temporary root must not be a symlink', verify_rpm)
        self.assertIn('temporary root is not a writable directory', verify_rpm)
        self.assertIn('failed to resolve temporary root', verify_rpm)
        self.assertNotIn('temporary root must be an absolute path: %s', verify_rpm)
        self.assertNotIn('temporary root must not be a symlink: %s', verify_rpm)
        self.assertNotIn('temporary root is not a writable directory: %s', verify_rpm)
        self.assertNotIn('failed to resolve temporary root: %s', verify_rpm)
        self.assertIn('tmp_dir_abs="$(realpath "${tmp_dir}")', verify_rpm)
        self.assertIn('temporary RPM verification directory escaped temporary root', verify_rpm)
        self.assertNotIn("${repo_dir}/.tmp", verify_rpm)

        self.assertIn('tmp_root="${TMPDIR:-/tmp}"', verify_dist)
        self.assertIn('if contains_control_chars "${tmp_root}"; then', verify_dist)
        self.assertIn('temporary root contains control characters', verify_dist)
        self.assertIn('temporary root must be an absolute path', verify_dist)
        self.assertIn('temporary root must not be a symlink', verify_dist)
        self.assertIn('temporary root is not a writable directory', verify_dist)
        self.assertIn('failed to resolve temporary root', verify_dist)
        self.assertNotIn('temporary root must be an absolute path: %s', verify_dist)
        self.assertNotIn('temporary root must not be a symlink: %s', verify_dist)
        self.assertNotIn('temporary root is not a writable directory: %s', verify_dist)
        self.assertNotIn('failed to resolve temporary root: %s', verify_dist)
        self.assertIn('tmp_dir_abs="$(realpath "${tmp_dir}")', verify_dist)
        self.assertIn('temporary dist verification directory escaped temporary root', verify_dist)
        self.assertNotIn("${repo_dir}/.tmp", verify_dist)

    def test_tmp_root_resolves_fail_closed_for_release_notes(self) -> None:
        publish_script = (REPO_ROOT / "scripts" / "publish-github-release.sh").read_text(encoding="utf-8")

        self.assertIn('notes_tmp_root="${TMPDIR:-/tmp}"', publish_script)
        self.assertIn('temporary root must be an absolute path:', publish_script)
        self.assertIn('temporary root must not be a symlink:', publish_script)
        self.assertIn('temporary root is not a writable directory:', publish_script)
        self.assertIn('failed to resolve temporary root:', publish_script)
        self.assertNotIn("${repo_dir}/.tmp", publish_script)

    def test_tag_release_workflow_publishes_verified_assets(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        publisher = (REPO_ROOT / "scripts" / "publish-github-release.sh").read_text(encoding="utf-8")

        self.assertIn('name: Release', workflow)
        self.assertIn('- "v*.*.*"', workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("run_workflow_lint:", workflow)
        self.assertNotIn("Skipping workflow lint", workflow)
        self.assertNotIn("RUN_WORKFLOW_LINT", workflow)
        self.assertIn("spelling-lint:", workflow)
        self.assertIn("crate-ci/typos", workflow)
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn(
            "publish:\n"
            "    needs:\n"
            "      - validate-release-tag\n"
            "      - workflow-lint\n"
            "      - spelling-lint\n"
            "      - security-scan",
            workflow,
        )
        self.assertIn(
            "publish:\n"
            "    needs:\n"
            "      - validate-release-tag\n"
            "      - workflow-lint\n"
            "      - spelling-lint\n"
            "      - security-scan\n"
            "    permissions:\n"
            "      contents: write",
            workflow,
        )
        self.assertIn("actions: none", workflow)
        self.assertIn("checks: none", workflow)
        self.assertIn("id-token: none", workflow)
        self.assertIn("issues: none", workflow)
        self.assertIn("packages: none", workflow)
        self.assertIn("pull-requests: none", workflow)
        self.assertIn("workflow-lint:", workflow)
        self.assertIn('version="1.7.12"', workflow)
        self.assertIn("rhysd/actionlint/releases/download/v${version}", workflow)
        self.assertIn("sha256sum -c -", workflow)
        self.assertIn('actionlint_version="1.7.12"', workflow)
        self.assertIn("rhysd/actionlint/releases/download/v${actionlint_version}", workflow)
        self.assertIn("Verify release tag provenance", workflow)
        self.assertIn('git rev-list -n 1 "${tag}^{commit}"', workflow)
        self.assertIn("security-scan:", workflow)
        self.assertIn("uses: ./.github/workflows/security-scan.yml", workflow)
        self.assertIn("with:\n      ref: ${{ needs.validate-release-tag.outputs.ref }}", workflow)
        self.assertIn(
            "needs:\n"
            "      - validate-release-tag\n"
            "      - workflow-lint\n"
            "      - spelling-lint\n"
            "      - security-scan",
            workflow,
        )
        self.assertIn('ACTIONLINT_STRICT: "true"', workflow)
        self.assertIn("fetch-depth: 0", workflow)
        self.assertIn("run: gh --version", workflow)
        self.assertIn("uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6", workflow)
        self.assertIn("uses: actions/setup-python@a309ff8b426b58ec0e2a45f0f869d46889d02405 # v6", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7", workflow)
        self.assertIn("runs-on: ubuntu-24.04", workflow)
        self.assertNotIn("runs-on: ubuntu-latest", workflow)
        self.assertIn("- name: Install release tooling", workflow)
        self.assertIn("run: make lint-workflows", workflow)
        self.assertIn("sudo apt-get update", workflow)
        self.assertIn(
            "python -m pip install --disable-pip-version-check --no-cache-dir --require-hashes -r .github/requirements/ci-bandit.txt",
            workflow,
        )
        self.assertIn("sudo apt-get install -y cpio rpm shellcheck squashfs-tools", workflow)
        self.assertIn("snap install snapcraft --classic", workflow)
        self.assertIn("run: make check", workflow)
        self.assertIn("run: make dist-check", workflow)
        self.assertIn("run: make rpm", workflow)
        self.assertIn("run: make rpm-check", workflow)
        self.assertIn("run: make rpm-generic", workflow)
        self.assertIn("run: make rpm-generic-check", workflow)
        self.assertTrue("if: env.BUILD_GENERIC_RPM == 'true'" in workflow or "if: env.BUILD_GENERIC_RPM == '1'" in workflow)
        self.assertIn("name: Build Snap package", workflow)
        self.assertIn('BUILD_SNAP: "1"', workflow)
        self.assertNotIn("build_snap:", workflow)
        self.assertNotIn("if: env.BUILD_SNAP", workflow)
        self.assertNotIn("--skip-snap", workflow)
        self.assertIn("build_generic_rpm:", workflow)
        self.assertIn("run: shellcheck scripts/*.sh", workflow)
        self.assertIn("GH_TOKEN: ${{ secrets.RELEASE_GITHUB_TOKEN || github.token }}", workflow)
        self.assertIn("RELEASE_TAG:", workflow)
        self.assertIn("args+=(--skip-generic-rpm)", workflow)
        self.assertIn('./scripts/publish-github-release.sh "${args[@]}" "${RELEASE_TAG}"', workflow)
        self.assertIn("build_generic_rpm", workflow)
        self.assertIn("printf 'tag<<TAG", workflow)
        self.assertIn("printf '%s\\n' \"${tag}\"", workflow)
        self.assertIn("printf 'TAG\\n'", workflow)
        self.assertIn("SNAPCRAFT_BASE<<SNAPCRAFT_BASE", workflow)
        self.assertIn('expected_tag="v${version}"', publisher)
        self.assertNotIn("::set-output", workflow)
        self.assertNotIn("::set-state", workflow)
        self.assertTrue(
            'snaps=(dist/snap/speed-of-cinnamon_"${version}"_*.snap)' in publisher
            or 'snaps=(dist/snap/speed-of-cinnamon_${version}_*.snap)' in publisher
        )
        self.assertIn("required_tools=(git python3 realpath awk sha256sum grep stat mktemp chmod basename dirname)", publisher)
        self.assertIn('safe_fs_cmd=(python3 "${safe_fs}")', publisher)
        self.assertIn('staging_dir_abs="$(realpath "${staging_dir}")', publisher)
        self.assertIn('release staging directory escaped dist directory', publisher)
        self.assertIn('notes_file_abs="$(realpath "${notes_file}")', publisher)
        self.assertIn('release notes file escaped temporary root', publisher)
        self.assertIn('existing_notes_file_abs="$(realpath "${existing_notes_file}")', publisher)
        self.assertIn('existing release notes file escaped temporary root', publisher)
        self.assertIn("fsync_regular_file() {", publisher)
        self.assertIn("write_regular_file_from_stdin() {", publisher)
        self.assertIn('write_regular_file_from_stdin "${notes_file}" "release notes file" <<EOF', publisher)
        self.assertIn('write_regular_file_from_stdin "${existing_notes_file}" "existing release notes file"', publisher)
        self.assertIn("flags = os.O_WRONLY | os.O_CREAT", publisher)
        self.assertIn("flags |= os.O_NOFOLLOW", publisher)
        self.assertNotIn("os.O_TRUNC", publisher)
        self.assertNotIn('cat > "${notes_file}"', publisher)
        self.assertNotIn('> "${existing_notes_file}"', publisher)
        self.assertIn('"${safe_fs_cmd[@]}" remove publish "${staging_dir}" --kind dir', publisher)
        self.assertIn('"${safe_fs_cmd[@]}" remove-leaf publish "${notes_file}"', publisher)
        self.assertIn('"${safe_fs_cmd[@]}" remove-leaf publish "${existing_notes_file}"', publisher)
        self.assertNotIn('rm -rf -- "${staging_dir}"', publisher)
        self.assertNotIn('rm -f -- "${notes_file}"', publisher)
        self.assertIn("if [[ \"${dry_run}\" == \"false\" ]]; then", publisher)
        self.assertIn("required_tools+=(gh)", publisher)
        self.assertIn("skip_generic=", publisher)
        self.assertIn("generic_rpms=(", publisher)
        self.assertIn("generic_srpms=(", publisher)
        self.assertIn("--skip-generic-rpm", publisher)
        self.assertIn("verify_asset_path() {", publisher)
        self.assertIn("asset is outside repository", publisher)
        self.assertIn("asset is not a regular file", publisher)
        self.assertIn("failed to resolve asset path", publisher)
        self.assertIn('realpath "${asset}" 2>/dev/null', publisher)
        self.assertNotIn("asset is outside repository: %s", publisher)
        self.assertNotIn("asset is not a regular file: %s", publisher)
        self.assertNotIn("asset must not be a symlink: %s", publisher)
        self.assertIn("if [[ -L \"${asset}\" ]];", publisher)
        self.assertIn("asset must not be a symlink", publisher)
        self.assertIn("checksum_target", publisher)
        self.assertIn("checksum file target mismatch", publisher)
        self.assertIn('checksum_dir="$(dirname "${checksum_ref}")"', publisher)
        self.assertIn('sha256sum --check --strict --status "${checksum_file}"', publisher)
        self.assertIn("checksum mismatch for", publisher)
        self.assertIn("gh release create", publisher)
        self.assertIn("gh release upload", publisher)
        self.assertIn("existing release assets are never overwritten", publisher)
        self.assertNotIn("--clobber", publisher)

    def test_ci_workflow_is_read_only(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

        self.assertIn("permissions:", workflow)
        self.assertIn("contents: read", workflow)
        self.assertNotIn("contents: write", workflow)
        self.assertNotIn("actions: none", workflow)
        self.assertNotIn("actions: write", workflow)
        self.assertNotIn("checks: write", workflow)
        self.assertNotIn("issues: write", workflow)
        self.assertNotIn("packages: write", workflow)
        self.assertNotIn("pull-requests: write", workflow)
        self.assertNotIn("id-token: write", workflow)

        self.assertIn("workflows_changed<<WORKFLOWS_CHANGED", workflow)

    def test_pylint_workflow_is_read_only(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "pylint.yml").read_text(encoding="utf-8")
        top_level_permissions = _workflow_block_lines(workflow, "permissions:")
        checkout_block = _workflow_block_lines(workflow, "- uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6")

        self.assertEqual(top_level_permissions, ["  contents: read"])
        self.assertEqual(checkout_block, [
            "        with:",
            "          persist-credentials: false",
        ])
        self.assertNotIn("permissions:", "\n".join(checkout_block))
        self.assertNotIn("contents: write", workflow)
        self.assertNotIn("id-token: write", workflow)
        self.assertNotIn("pull-requests: write", workflow)

    def test_scorecard_workflow_does_not_request_oidc_token(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "scorecard.yml").read_text(encoding="utf-8")
        self.assertIn("security-events: write", workflow)
        self.assertNotIn("id-token: write", workflow)
        self.assertIn("publish_results: false", workflow)

    def test_bandit_workflow_runs_native_blocking_scan_without_extra_token_scope(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "bandit.yml").read_text(encoding="utf-8")
        self.assertIn("runs-on: ubuntu-24.04", workflow)
        self.assertNotIn("ubuntu-latest", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("actions/setup-python@a309ff8b426b58ec0e2a45f0f869d46889d02405 # v6", workflow)
        self.assertIn(".github/requirements/ci-bandit.txt", workflow)
        self.assertIn("--require-hashes", workflow)
        self.assertIn("run: make python-security-scan", workflow)
        self.assertNotIn("security-events: write", workflow)
        self.assertNotIn("exit_zero: true", workflow)
        self.assertNotIn("shundor/python-bandit-scan", workflow)
        self.assertNotIn("GITHUB_TOKEN:", workflow)
        self.assertNotIn("secrets.GITHUB_TOKEN", workflow)

    def test_workflow_actions_are_pinned_or_explicitly_reviewed(self) -> None:
        allowed_mutable_refs: set[str] = set()
        offenders: list[str] = []
        for path in sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")):
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                match = re.search(r"\buses:\s*[\"']?([^\"'\s#]+)", line)
                if not match:
                    continue
                ref = match.group(1)
                if ref.startswith("./"):
                    continue
                if "@" not in ref:
                    offenders.append(f"{path}:{line_number}: action reference must include @ref")
                    continue
                pinned_ref = ref.rsplit("@", 1)[1]
                if re.fullmatch(r"[0-9a-fA-F]{40}", pinned_ref):
                    continue
                if ref not in allowed_mutable_refs:
                    offenders.append(f"{path}:{line_number}: mutable action ref must be reviewed explicitly: {ref}")

        self.assertEqual(offenders, [])

    def test_workflows_install_pinned_actionlint_release(self) -> None:
        ci_workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        lint_workflow = (REPO_ROOT / ".github" / "workflows" / "super-linter.yml").read_text(encoding="utf-8")
        linter = (REPO_ROOT / "scripts" / "lint-workflows.sh").read_text(encoding="utf-8")
        self.assertIn('version="1.7.12"', ci_workflow)
        self.assertIn('version="1.7.12"', lint_workflow)
        self.assertIn("rhysd/actionlint/releases/download/v${version}", ci_workflow)
        self.assertIn("rhysd/actionlint/releases/download/v${version}", lint_workflow)
        self.assertIn("archive_sha256=\"8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8\"", ci_workflow)
        self.assertIn("archive_sha256=\"8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8\"", lint_workflow)
        self.assertIn("sha256sum -c -", ci_workflow)
        self.assertIn("sha256sum -c -", lint_workflow)
        self.assertGreaterEqual(ci_workflow.count("sudo install -m 0755 /tmp/actionlint /usr/local/bin/actionlint"), 2)
        self.assertNotIn("rhysd/actionlint:", linter)
        self.assertNotIn("latest", linter)

    def test_local_release_targets_support_generic_rpm_toggle(self) -> None:
        makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn("BUILD_GENERIC_RPM ?= 1", makefile)
        self.assertIn("if [ \"$(BUILD_GENERIC_RPM)\" = \"0\" ]; then", makefile)
        self.assertIn("Skipping generic RPM generation (BUILD_GENERIC_RPM=0).\\n", makefile)
        self.assertIn("--skip-generic-rpm", makefile)
        self.assertIn("release-dry-run: release-validate-flags release-require-snap dist-check rpm rpm-check", makefile)
        self.assertIn("release: release-validate-flags release-require-snap dist-check rpm rpm-check", makefile)
        self.assertIn("release-validate-flags", makefile)
        self.assertIn("release-validate-flags:", makefile)
        self.assertIn('SNAP_BUILD must be 0 or 1.\\n', makefile)
        self.assertIn('BUILD_GENERIC_RPM must be 0 or 1.\\n', makefile)
        self.assertIn("release-require-snap:", makefile)
        self.assertIn("SNAP_BUILD=0 is not allowed for release or release-dry-run.", makefile)
        self.assertIn("release-dry-run-no-snap:", makefile)

    def test_build_rpm_stages_topdir_before_replacing_previous_build(self) -> None:
        build_rpm = (REPO_ROOT / "scripts" / "build-rpm.sh").read_text(encoding="utf-8")
        self.assertIn('final_topdir="${repo_dir}/dist/rpmbuild"', build_rpm)
        self.assertIn(
            'stage_topdir="$(mktemp -d "${rpmbuild_tmpdir}/.$(basename "${final_topdir}").stage.XXXXXX")"',
            build_rpm,
        )
        self.assertIn('--define "_topdir ${stage_topdir}"', build_rpm)
        self.assertIn('safe_fs="${repo_dir}/scripts/safe-local-fs.py"', build_rpm)
        self.assertIn('require_regular_source_file "${safe_fs}" "safe local filesystem helper"', build_rpm)
        self.assertIn('safe_fs_cmd=(python3 "${safe_fs}")', build_rpm)
        self.assertIn('rpmbuild_tmpdir_abs="$(realpath "${rpmbuild_tmpdir}")', build_rpm)
        self.assertIn('temporary RPM workspace escaped temporary root', build_rpm)
        self.assertIn('stage_topdir_abs="$(realpath "${stage_topdir}")', build_rpm)
        self.assertIn('temporary RPM stage directory escaped temporary workspace', build_rpm)
        self.assertIn('dist_finalize_lock="${dist_dir}/.build-rpm.finalize.lock"', build_rpm)
        self.assertIn("activate_with_finalize_lock() {", build_rpm)
        self.assertIn("import fcntl", build_rpm)
        self.assertNotIn("spec_path.write_text", build_rpm)
        self.assertIn("os.replace(tmp_name, spec_path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)", build_rpm)
        self.assertIn("os.fsync(parent_fd)", build_rpm)
        self.assertIn('activate_with_finalize_lock "${dist_finalize_lock}" "${stage_topdir}" "${final_topdir}"', build_rpm)
        self.assertIn('[sys.executable, safe_fs, "install-tree", "build-rpm", staging_path, final_path, "RPM build directory"]', build_rpm)
        self.assertIn('"${safe_fs_cmd[@]}" remove build-rpm "${stage_topdir}" --kind dir', build_rpm)
        self.assertIn('"${safe_fs_cmd[@]}" remove build-rpm "${rpmbuild_tmpdir}" --kind dir', build_rpm)
        self.assertNotIn('mv -T -- "${stage_topdir}" "${final_topdir}"', build_rpm)
        self.assertNotIn('rm -rf "${topdir}"', build_rpm)
        self.assertNotIn('rm -rf -- "${stage_topdir}"', build_rpm)
        self.assertNotIn('rm -rf -- "${rpmbuild_tmpdir}"', build_rpm)

    def test_parallel_build_dist_does_not_corrupt_archive(self) -> None:
        build_dist = REPO_ROOT / "scripts" / "build-dist.sh"
        build_dist_source = build_dist.read_text(encoding="utf-8")
        self.assertIn('safe_fs="${repo_dir}/scripts/safe-local-fs.py"', build_dist_source)
        self.assertIn('safe_fs_cmd=(python3 "${safe_fs}")', build_dist_source)
        self.assertIn('work_dir_abs="$(realpath "${work_dir}")', build_dist_source)
        self.assertIn('temporary build-dist workspace escaped temporary root', build_dist_source)
        self.assertIn('"${safe_fs_cmd[@]}" remove build-dist "${work_dir}" --kind dir', build_dist_source)
        self.assertIn('"${safe_fs_cmd[@]}" remove build-dist "${cache_dir}" --kind dir', build_dist_source)
        self.assertIn('"${safe_fs_cmd[@]}" remove build-dist "${bytecode_file}" --kind file', build_dist_source)
        self.assertIn(
            'python3 "${safe_fs}" install-tree build-dist "${source_path}" "${target_path}"',
            build_dist_source,
        )
        self.assertIn(
            'python3 "${safe_fs}" copy-file build-dist "${source_path}" "${target_path}" 0644',
            build_dist_source,
        )
        self.assertNotIn('cp -a "${repo_dir}/${path}" "${work_dir}/${package}/"', build_dist_source)
        self.assertNotIn('rm -rf -- "${work_dir}"', build_dist_source)
        self.assertNotIn("-exec rm -rf {} +", build_dist_source)
        self.assertIn('staging_checksum="$(mktemp "${dist_dir}/.${package}.tar.gz.sha256.XXXXXX")"', build_dist_source)
        self.assertIn("fsync_regular_file() {", build_dist_source)
        self.assertIn("write_regular_file_from_stdin() {", build_dist_source)
        self.assertIn('write_regular_file_from_stdin "${work_dir}/${package}/RELEASE-MANIFEST.txt" "release manifest" <<EOF', build_dist_source)
        self.assertIn('write_regular_file_from_stdin "${staging_checksum}" "staged dist checksum"', build_dist_source)
        self.assertIn("flags = os.O_WRONLY | os.O_CREAT", build_dist_source)
        self.assertIn("flags |= os.O_NOFOLLOW", build_dist_source)
        self.assertNotIn("os.O_TRUNC", build_dist_source)
        self.assertNotIn('cat > "${work_dir}/${package}/RELEASE-MANIFEST.txt"', build_dist_source)
        self.assertNotIn('> "${staging_checksum}"', build_dist_source)
        self.assertIn('fsync_regular_file "${staging_tarball}" "staged dist tarball"', build_dist_source)
        self.assertIn("dist_finalize_lock=\"${dist_dir}/.build-dist.finalize.lock\"", build_dist_source)
        self.assertIn("replace_with_finalize_lock() {", build_dist_source)
        self.assertIn("import fcntl", build_dist_source)
        self.assertIn('"${dist_finalize_lock}"', build_dist_source)
        self.assertIn('  "${staging_tarball}" \\\n  "${final_tarball}" \\\n  "${staging_checksum}" \\\n  "${final_checksum}"', build_dist_source)
        self.assertNotIn('mv -T -- "${staging_checksum}" "${final_checksum}"', build_dist_source)
        self.assertNotIn('> "${final_checksum}"', build_dist_source)

        def run_build() -> tuple[int, str]:
            proc = subprocess.run(
                [str(build_dist)],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if proc.stdout:
                return proc.returncode, proc.stdout
            return proc.returncode, proc.stderr

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(run_build) for _ in range(2)]
            results = [future.result() for future in futures]

        for code, _ in results:
            self.assertEqual(code, 0)
        pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r'^version = "([^"]+)"$', pyproject, flags=re.MULTILINE)
        self.assertIsNotNone(match)
        version = match.group(1)
        tarball = REPO_ROOT / "dist" / f"speed-of-cinnamon-{version}.tar.gz"
        checksum = tarball.with_suffix(tarball.suffix + ".sha256")
        self.assertTrue(tarball.exists())
        self.assertTrue(checksum.exists())
        checksum_text = checksum.read_text(encoding="utf-8")
        self.assertIn(f"  speed-of-cinnamon-{version}.tar.gz\n", checksum_text)
        self.assertNotIn(f"  dist/speed-of-cinnamon-{version}.tar.gz\n", checksum_text)
        subprocess.run(
            ["sha256sum", "--check", "--strict", "--status", checksum.name],
            cwd=checksum.parent,
            check=True,
        )
        with tempfile.TemporaryDirectory() as tmp:
            download_dir = Path(tmp)
            copied_tarball = download_dir / tarball.name
            copied_checksum = download_dir / checksum.name
            shutil.copy2(tarball, copied_tarball)
            shutil.copy2(checksum, copied_checksum)
            subprocess.run(
                ["sha256sum", "--check", "--strict", "--status", copied_checksum.name],
                cwd=download_dir,
                check=True,
            )

        outputs = [output for _, output in results]
        tarball_paths = set()
        for output in outputs:
            match = re.search(r"Built (.+)", output)
            if not match:
                stripped = output.strip()
                if stripped.endswith(".tar.gz") and stripped.startswith("/"):
                    tarball_paths.add(stripped)
                    continue
                match = None
            self.assertIsNotNone(match, f"expected build output to include built archive path: {output!r}")
            tarball_paths.add(match.group(1).strip())

        self.assertEqual(len(tarball_paths), 1)
        tarball = Path(next(iter(tarball_paths)))
        self.assertTrue(tarball.exists())
        self.assertTrue(tarball.stat().st_size > 0)
        with tarfile.open(tarball, "r:gz") as archive:
            self.assertTrue(any(member.isdir() for member in archive.getmembers()))
