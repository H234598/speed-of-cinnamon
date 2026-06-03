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

    def test_command_chain_security_tests_are_present(self) -> None:
        command_chain_test = REPO_ROOT / "tests" / "test_command_chain.py"
        self.assertTrue(command_chain_test.exists(), "command chain security tests must exist")
        text = command_chain_test.read_text(encoding="utf-8")
        self.assertIn("class CommandChainTest", text)
        self.assertIn("unsupported shell operator", text)

    def test_runtime_code_does_not_execute_subprocess_with_shell_strings(self) -> None:
        src_root = REPO_ROOT / "src"
        offenders = []
        for path in src_root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            offenders.extend(_subprocess_security_offenders(path, tree))

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
            '"  sudo rm -rf /usr/share/ollama",',
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
        self.assertIn("needs:\n      - workflow-lint\n      - spelling-lint\n      - security-scan", workflow)
        self.assertIn("- spelling-lint", workflow)
        self.assertIn('ACTIONLINT_STRICT: "true"', workflow)
        self.assertIn("build_generic_rpm:", workflow)
        self.assertIn("fetch-depth: 0", workflow)
        self.assertIn("QLTY_COVERAGE_TOKEN: ${{ secrets.QLTY_COVERAGE_TOKEN }}", workflow)
        self.assertIn("run: python -m pip install coverage", workflow)
        self.assertIn("run: make check", workflow)
        self.assertIn("run: make coverage", workflow)
        self.assertNotIn("curl -fsSL https://qlty.sh | sh", workflow)
        self.assertIn("curl -fsSLo \"${qlty_installer}\" https://qlty.sh", workflow)
        self.assertIn("gh attestation verify \"${qlty_installer}\" --owner qltysh", workflow)
        self.assertIn("sh \"${qlty_installer}\"", workflow)
        self.assertIn('"${HOME}/.qlty/bin/qlty" coverage publish reports/lcov.info', workflow)
        self.assertIn("if: ${{ env.QLTY_COVERAGE_TOKEN != '' }}", workflow)
        self.assertIn("GH_TOKEN: ${{ github.token }}", workflow)
        self.assertIn("sudo apt-get update", workflow)
        self.assertIn("sudo apt-get install -y cpio rpm shellcheck", workflow)
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
        self.assertIn("python -m pip install --disable-pip-version-check --no-cache-dir 'bandit[sarif]==1.9.4'", codacy_workflow)
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
        self.assertEqual(security_workflow.count("ref: ${{ inputs.ref || github.ref }}"), 2)
        self.assertIn("timeout-minutes: 15", security_workflow)
        self.assertIn("timeout-minutes: 10", security_workflow)
        self.assertIn("python-security:", security_workflow)
        self.assertIn("shell-security:", security_workflow)
        self.assertIn("run: python -m pip install --disable-pip-version-check --no-cache-dir bandit==1.9.4", security_workflow)
        self.assertIn("run: make python-security-scan", security_workflow)
        self.assertIn("run: make shell-security-scan", security_workflow)
        self.assertIn("run: |\n          sudo apt-get update\n          sudo apt-get install -y --no-install-recommends shellcheck", security_workflow)
        self.assertIn("permissions:", security_workflow)
        self.assertIn("contents: read", security_workflow)
        self.assertNotIn("contents: write", security_workflow)

    def test_release_workflow_validates_tag_against_pyproject_version(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

        self.assertIn('tags:\n      - "v*.*.*"', workflow)
        checkout_ref = "ref: ${{ github.event_name == 'workflow_dispatch' && format('refs/tags/{0}', inputs.tag) || github.ref }}"
        self.assertEqual(workflow.count(checkout_ref), 4)
        self.assertIn("with:\n      ref: ${{ github.event_name == 'workflow_dispatch' && format('refs/tags/{0}', inputs.tag) || github.ref }}", workflow)
        self.assertIn("format('refs/tags/{0}', inputs.tag)", workflow)
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
        self.assertIn("existing_release_title=", publish_script)
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
        build_rpm = (REPO_ROOT / "scripts" / "build-rpm.sh").read_text(encoding="utf-8")
        build_snap = (REPO_ROOT / "scripts" / "build-snap.sh").read_text(encoding="utf-8")
        uninstall_local = (REPO_ROOT / "scripts" / "uninstall-local.sh").read_text(encoding="utf-8")

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
        self.assertIn('install-tree build-snap "${repo_dir}/snap" "${snap_workspace}/snap" "snap source tree"', build_snap)
        self.assertIn('install-tree build-snap "${repo_dir}/src" "${snap_workspace}/src" "Python source tree"', build_snap)
        self.assertIn('copy-file build-snap "${repo_dir}/pyproject.toml" "${snap_workspace}/pyproject.toml" 0644', build_snap)
        self.assertIn('copy-file build-snap "${repo_dir}/README.md" "${snap_workspace}/README.md" 0644', build_snap)
        self.assertNotIn('install-tree build-snap "${repo_dir}" "${snap_workspace}" "snap temporary source tree"', build_snap)
        self.assertIn('python3 - "${snapcraft_file_rendered}" "${snapcraft_file_rendered}" "${version}" "${snapcraft_base}"', build_snap)
        self.assertIn('( cd "${snap_workspace}" && umask 022 && snapcraft pack --destructive-mode )', build_snap)
        self.assertIn('python3 "${safe_fs}" replace build-snap "${snap_files[0]}" "${output_path}" --src-kind file --dst-must-not-exist', build_snap)
        self.assertNotIn("mv -T", build_snap)
        self.assertIn('safe_fs rmdir uninstall "${app_data}" --ignore-non-empty', uninstall_local)
        self.assertNotIn('safe_fs rmdir uninstall "${app_data}" --ignore-non-empty || true', uninstall_local)

    def test_publish_script_stages_all_assets_before_release_upload(self) -> None:
        publish_script = (REPO_ROOT / "scripts" / "publish-github-release.sh").read_text(encoding="utf-8")

        self.assertIn("upload_refs=()", publish_script)
        self.assertIn('for asset in "${assets[@]}"; do', publish_script)
        self.assertIn('staged_path="${staging_dir}/${staged_name}"', publish_script)
        self.assertIn('if [[ "${asset}" == "${generic_asset}" ]]; then', publish_script)
        self.assertIn('staged_name="$(generic_asset_label "${asset}")"', publish_script)
        self.assertIn('asset_abs="$(realpath "${asset}")"', publish_script)
        self.assertIn('copy-file publish "${asset_abs}" "${staged_path}" 0644', publish_script)
        self.assertIn('verify_asset_path "${staged_path}"', publish_script)
        self.assertIn("chmod 0444 -- \"${staged_path}\"", publish_script)
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
        self.assertIn("GITHUB_REPOSITORY is not set and origin is not a GitHub repository.", publish_script)
        self.assertIn("repository value does not match checked out origin", publish_script)
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
        self.assertIn("python-security-scan:\n\tbandit -q -r src/speed_of_cinnamon -x tests", makefile)
        self.assertIn("shell-security-scan:\n\tshellcheck scripts/*.sh", makefile)
        self.assertIn("security-scan: python-security-scan shell-security-scan", makefile)
        self.assertIn('expected_name = "H234598"', verifier)
        self.assertIn('expected_email = "54270221+H234598@users.noreply.github.com"', verifier)
        self.assertIn('expected_repo = "github.com/H234598/speed-of-cinnamon"', verifier)
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
        self.assertIn("lint-workflows-check:", makefile)
        self.assertIn("lint-workflows-check", makefile)

        self.assertIn("\t@if [ \"$${GITHUB_ACTIONS:-false}\" = \"true\" ]; then \\", makefile)
        self.assertIn("\t  if [ \"$${GITHUB_ACTIONS:-false}\" = \"true\" ]; then \\", makefile)
        self.assertIn("./scripts/lint-workflows.sh \\", makefile)
        self.assertIn("workflow lint skipped locally; install actionlint for strict checks.", makefile)

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
        self.assertIn("%{_mandir}/man1/speed-of-cinnamon.1*", spec)
        self.assertIn("%{_mandir}/man1/speed-of-cinnamon-alarms.1*", spec)
        self.assertIn("speed-of-cinnamon\\.1(\\.gz)?", rpm_verifier)
        self.assertIn("speed-of-cinnamon-alarms\\.1(\\.gz)?", rpm_verifier)
        self.assertIn("docs/man/speed-of-cinnamon.1", install_local)
        self.assertIn('SPEED_OF_CINNAMON_TEST_HOME:-0', install_local)
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
        self.assertIn('safe_fs write-wrapper install "${stage_root}/speed-of-cinnamon/bin/speed-of-cinnamon" "${app_data}/python"', install_local)
        self.assertIn('safe_fs install-tree install "${source_root}/src/speed_of_cinnamon" "${stage_root}/speed-of-cinnamon/python/speed_of_cinnamon" "python package"', install_local)
        self.assertIn('activated_had_existing+=("0")', install_local)
        self.assertIn('remove install "${target}" --kind "${kind}"', install_local)
        self.assertNotIn('mktemp -d "${parent}/.${name}.install.', install_local)
        self.assertIn('safe_fs copy-file install "${source_root}/docs/man/speed-of-cinnamon.1"', install_local)
        self.assertIn("python3 -m compileall -q", dist_verifier)
        self.assertIn('${package_dir}/scripts/safe-local-fs.py', dist_verifier)
        self.assertIn("archive backend wrapper helper does not invoke the expected CLI module", dist_verifier)
        self.assertIn("scripts/safe-local-fs.py", dist_verifier)
        self.assertIn('exec "$(command -v -- python3)" -m speed_of_cinnamon.cli "$@"', dist_verifier)
        self.assertIn("RPM package contains unsafe path entry", rpm_verifier)
        self.assertIn("RPM expansion contains unsupported symlink entries.", rpm_verifier)
        self.assertIn("RPM expansion contains unsupported hardlink entries.", rpm_verifier)
        self.assertIn("RPM package must be a regular file", rpm_verifier)
        self.assertIn('safe_fs="${repo_dir}/scripts/safe-local-fs.py"', rpm_verifier)
        self.assertIn('"${safe_fs_cmd[@]}" copy-file verify-rpm "${rpm_path}" "${rpm_snapshot}" 0644', rpm_verifier)
        self.assertIn('rpm_snapshot="${tmp_dir}/speed-of-cinnamon-verify.rpm"', rpm_verifier)
        self.assertIn('rpm -qp --qf', rpm_verifier)
        self.assertIn('"${rpm_snapshot}" > "${metadata_file}"', rpm_verifier)
        self.assertIn('rpm -qpl "${rpm_snapshot}" > "${file_list}"', rpm_verifier)
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
        self.assertIn("tarfile.open(tarball, \"r:gz\")", dist_verifier)
        self.assertIn("member.issym()", dist_verifier)
        self.assertIn("member.islnk()", dist_verifier)
        self.assertIn("raise SystemExit(f\"dist archive contains unsupported link entry", dist_verifier)
        self.assertIn("package_root = None", dist_verifier)
        self.assertIn("dist archive contains multiple top-level entries", dist_verifier)
        self.assertIn("dist archive contains an empty path entry", dist_verifier)

    def test_dev_backend_path_does_not_append_env_pythonpath(self) -> None:
        dev_backend = (REPO_ROOT / "scripts" / "dev-backend.sh").read_text(encoding="utf-8")
        self.assertNotIn("PYTHONPATH:+", dev_backend)
        self.assertIn('export PYTHONPATH="${repo_dir}/src"', dev_backend)

    def test_build_snap_rejects_symlinked_snap_dir(self) -> None:
        build_snap = (REPO_ROOT / "scripts" / "build-snap.sh").read_text(encoding="utf-8")
        verify_snap = (REPO_ROOT / "scripts" / "verify-snap.sh").read_text(encoding="utf-8")
        self.assertIn('snap_dir="${repo_dir}/snap"', build_snap)
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
        self.assertIn('snap temporary root must be outside repository', build_snap)
        self.assertIn('snap_workspace="$(mktemp -d "${repo_tmp_root}/speed-of-cinnamon-snap-tree-XXXXXX")"', build_snap)
        self.assertIn('snapcraft_file_rendered="${snap_workspace}/snap/snapcraft.yaml"', build_snap)
        self.assertIn('install-tree build-snap "${repo_dir}/snap" "${snap_workspace}/snap" "snap source tree"', build_snap)
        self.assertIn('install-tree build-snap "${repo_dir}/src" "${snap_workspace}/src" "Python source tree"', build_snap)
        self.assertIn('copy-file build-snap "${repo_dir}/pyproject.toml" "${snap_workspace}/pyproject.toml" 0644', build_snap)
        self.assertIn('copy-file build-snap "${repo_dir}/README.md" "${snap_workspace}/README.md" 0644', build_snap)
        self.assertNotIn('install-tree build-snap "${repo_dir}" "${snap_workspace}" "snap temporary source tree"', build_snap)
        self.assertIn('mkdir -p "${snap_workspace_dist}"', build_snap)
        self.assertIn('python3 - "${snapcraft_file_rendered}" "${snapcraft_file_rendered}" "${version}" "${snapcraft_base}"', build_snap)
        self.assertIn('python3 "${safe_fs}" replace build-snap "${snap_files[0]}" "${output_path}" --src-kind file --dst-must-not-exist', build_snap)
        self.assertNotIn('snapcraft_backup', build_snap)
        self.assertNotIn('mv -f -- "${snapcraft_backup}" "${snapcraft_file}"', build_snap)
        self.assertNotIn("mv -T", build_snap)
        self.assertNotIn("NamedTemporaryFile", build_snap)
        self.assertIn('refusing to overwrite existing snap artifact for version', build_snap)
        self.assertIn('if find "${dist_dir}" "${repo_dir}" -maxdepth 1 -name "speed-of-cinnamon_${version}_*.snap" -print -quit | grep -q .; then', build_snap)
        self.assertNotIn('rm -f -- "${dist_dir}/speed-of-cinnamon_${version}"_*.snap', build_snap)
        self.assertNotIn('path.with_name(path.name + ".tmp")', build_snap)
        self.assertIn('snap_dir="${repo_dir}/dist/snap"', verify_snap)
        self.assertIn('if [[ -L "${snap_dir}" ]]; then', verify_snap)
        self.assertIn('snap directory must not be a symlink', verify_snap)
        self.assertIn("snap file must not be hardlinked", verify_snap)
        self.assertIn('$\'\\n\'', verify_snap)
        self.assertIn('snap file path contains control characters', verify_snap)

    def test_build_snap_and_verify_rpm_guard_paths_against_canonical_repo_dir(self) -> None:
        build_snap = (REPO_ROOT / "scripts" / "build-snap.sh").read_text(encoding="utf-8")
        verify_rpm = (REPO_ROOT / "scripts" / "verify-rpm.sh").read_text(encoding="utf-8")

        self.assertIn('repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"', build_snap)
        self.assertIn('repo_tmp_abs="$(realpath "${repo_tmp_root}")"', build_snap)
        self.assertIn('if [[ "${repo_tmp_abs}" == "${repo_dir}" || "${repo_tmp_abs}" == "${repo_dir}/"* ]]; then', build_snap)
        self.assertIn('repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"', verify_rpm)
        self.assertIn('if ! rpm_path="$(realpath "${rpm_path}")"; then', verify_rpm)
        self.assertIn('if [[ -L "${rpm_path}" || ! -f "${rpm_path}" || ! ( "${rpm_path}" == "${repo_dir}/dist/rpmbuild/"*".rpm" ||', verify_rpm)

    def test_verify_rpm_uses_private_snapshot_for_rpm_tooling(self) -> None:
        verify_rpm = (REPO_ROOT / "scripts" / "verify-rpm.sh").read_text(encoding="utf-8")
        snapshot_copy = verify_rpm.index('"${safe_fs_cmd[@]}" copy-file verify-rpm "${rpm_path}" "${rpm_snapshot}" 0644')
        metadata_check = verify_rpm.index('"${rpm_snapshot}" > "${metadata_file}"')
        file_list_check = verify_rpm.index('rpm -qpl "${rpm_snapshot}" > "${file_list}"')
        extraction_check = verify_rpm.index('rpm2cpio "${rpm_snapshot}" | cpio -idmu --no-absolute-filenames --quiet')

        self.assertLess(snapshot_copy, metadata_check)
        self.assertLess(snapshot_copy, file_list_check)
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
        self.assertIn('temporary root must be an absolute path:', verify_rpm)
        self.assertIn('temporary root must not be a symlink:', verify_rpm)
        self.assertIn('temporary root is not a writable directory:', verify_rpm)
        self.assertIn('failed to resolve temporary root:', verify_rpm)
        self.assertNotIn("${repo_dir}/.tmp", verify_rpm)

        self.assertIn('tmp_root="${TMPDIR:-/tmp}"', verify_dist)
        self.assertIn('temporary root must be an absolute path:', verify_dist)
        self.assertIn('temporary root must not be a symlink:', verify_dist)
        self.assertIn('temporary root is not a writable directory:', verify_dist)
        self.assertIn('failed to resolve temporary root:', verify_dist)
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
        self.assertIn("run_workflow_lint:", workflow)
        self.assertIn("spelling-lint:", workflow)
        self.assertIn("crate-ci/typos", workflow)
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn("publish:\n    needs:\n      - workflow-lint\n      - spelling-lint\n      - security-scan", workflow)
        self.assertIn(
            "publish:\n    needs:\n      - workflow-lint\n      - spelling-lint\n      - security-scan\n    permissions:\n      contents: write",
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
        self.assertIn('actionlint_version="1.7.12"', workflow)
        self.assertIn("rhysd/actionlint/releases/download/v${actionlint_version}", workflow)
        self.assertIn("Verify release tag provenance", workflow)
        self.assertIn('git rev-list -n 1 "${tag}^{commit}"', workflow)
        self.assertIn("security-scan:", workflow)
        self.assertIn("uses: ./.github/workflows/security-scan.yml", workflow)
        self.assertIn("with:\n      ref: ${{ github.event_name == 'workflow_dispatch' && format('refs/tags/{0}', inputs.tag) || github.ref }}", workflow)
        self.assertIn("needs:\n      - workflow-lint\n      - spelling-lint\n      - security-scan", workflow)
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
        self.assertIn("run: |", workflow)
        self.assertIn("sudo apt-get update", workflow)
        self.assertIn("python -m pip install --disable-pip-version-check --no-cache-dir bandit==1.9.4", workflow)
        self.assertIn("sudo apt-get install -y cpio rpm shellcheck", workflow)
        self.assertIn("snap install snapcraft --classic", workflow)
        self.assertIn("run: make check", workflow)
        self.assertIn("run: make dist-check", workflow)
        self.assertIn("run: make rpm", workflow)
        self.assertIn("run: make rpm-check", workflow)
        self.assertIn("run: make rpm-generic", workflow)
        self.assertIn("run: make rpm-generic-check", workflow)
        self.assertTrue("if: env.BUILD_GENERIC_RPM == 'true'" in workflow or "if: env.BUILD_GENERIC_RPM == '1'" in workflow)
        self.assertIn("name: Build Snap package", workflow)
        self.assertIn("github.event_name != 'workflow_dispatch' && '0'", workflow)
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
        self.assertIn("required_tools=(git python3 realpath awk sha256sum grep stat mktemp chmod)", publisher)
        self.assertIn("if [[ \"${dry_run}\" == \"false\" ]]; then", publisher)
        self.assertIn("required_tools+=(gh)", publisher)
        self.assertIn("skip_generic=", publisher)
        self.assertIn("generic_rpms=(", publisher)
        self.assertIn("generic_srpms=(", publisher)
        self.assertIn("--skip-generic-rpm", publisher)
        self.assertIn("verify_asset_path() {", publisher)
        self.assertIn("asset is outside repository", publisher)
        self.assertIn("asset is not a regular file", publisher)
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
        self.assertIn("bandit==1.9.4", workflow)
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
        self.assertIn("release-dry-run: release-validate-flags dist-check rpm rpm-check", makefile)
        self.assertIn("release: release-validate-flags dist-check rpm rpm-check", makefile)
        self.assertIn("release-validate-flags", makefile)
        self.assertIn("release-validate-flags:", makefile)
        self.assertIn('SNAP_BUILD must be 0 or 1.\\n', makefile)
        self.assertIn('BUILD_GENERIC_RPM must be 0 or 1.\\n', makefile)

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
        self.assertIn('dist_finalize_lock="${dist_dir}/.build-rpm.finalize.lock"', build_rpm)
        self.assertIn("activate_with_finalize_lock() {", build_rpm)
        self.assertIn("import fcntl", build_rpm)
        self.assertIn('activate_with_finalize_lock "${dist_finalize_lock}" "${stage_topdir}" "${final_topdir}"', build_rpm)
        self.assertIn('[sys.executable, safe_fs, "install-tree", "build-rpm", staging_path, final_path, "RPM build directory"]', build_rpm)
        self.assertNotIn('mv -T -- "${stage_topdir}" "${final_topdir}"', build_rpm)
        self.assertNotIn('rm -rf "${topdir}"', build_rpm)

    def test_parallel_build_dist_does_not_corrupt_archive(self) -> None:
        build_dist = REPO_ROOT / "scripts" / "build-dist.sh"
        build_dist_source = build_dist.read_text(encoding="utf-8")
        self.assertIn('safe_fs="${repo_dir}/scripts/safe-local-fs.py"', build_dist_source)
        self.assertIn('require_unsafe_source "${safe_fs}" "safe local filesystem helper"', build_dist_source)
        self.assertIn('staging_checksum="$(mktemp "${dist_dir}/.${package}.tar.gz.sha256.XXXXXX")"', build_dist_source)
        self.assertIn('printf \'%s  %s\\n\' "${checksum_value}" "${package}.tar.gz" > "${staging_checksum}"', build_dist_source)
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
