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


def _is_command_sequence(node: ast.AST | None) -> bool:
    if isinstance(node, (ast.List, ast.Tuple)):
        return True
    if isinstance(node, ast.Name):
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"list", "tuple"}:
        return len(node.args) == 1 and isinstance(node.args[0], (ast.List, ast.Tuple))
    return False


def _is_safe_env_argument(node: ast.AST | None) -> bool:
    if node is None:
        return True
    if isinstance(node, ast.Name):
        return True
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id == "dict":
            return False
        return True
    if not isinstance(node, ast.Dict):
        return False
    for key in node.keys:
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            return False
    for value in node.values:
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            return False
    return True


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
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not isinstance(func, ast.Attribute) or not isinstance(func.value, ast.Name):
                    continue
                if func.value.id != "subprocess":
                    continue
                if func.attr not in {"run", "Popen", "call", "check_call", "check_output"}:
                    continue

                for kw in node.keywords:
                    if kw.arg == "shell":
                        if not (isinstance(kw.value, ast.Constant) and kw.value.value is False):
                            offenders.append(f"{path}: {func.attr} with unsupported shell value")
                        continue
                    if kw.arg == "env":
                        if not _is_safe_env_argument(kw.value):
                            offenders.append(f"{path}: {func.attr} env must be a prepared value or string literal dict")

                command = None
                if node.args:
                    command = node.args[0]
                else:
                    for kw in node.keywords:
                        if kw.arg == "args":
                            command = kw.value
                            break
                if isinstance(command, ast.Constant) and isinstance(command.value, str):
                    offenders.append(f"{path}: {func.attr} first arg is string constant")
                elif isinstance(command, ast.JoinedStr):
                    offenders.append(f"{path}: {func.attr} first arg is f-string")
                elif not _is_command_sequence(command):
                    offenders.append(f"{path}: {func.attr} command must be list/tuple")

        self.assertFalse(offenders, f"unsafe subprocess usage found: {offenders}")

    def test_command_sequence_validation_accepts_supported_forms(self) -> None:
        allowed = ["[\"a\", \"b\"]", "(\"a\", \"b\")", "list([\"a\", \"b\"])", "tuple((\"a\", \"b\"))", "command"]
        blocked = ["tuple('a',)", "list()", "list('ab')", "tuple(command)"]

        for expr in allowed:
            node = ast.parse(expr, mode="eval").body
            self.assertTrue(_is_command_sequence(node))
        for expr in blocked:
            node = ast.parse(expr, mode="eval").body
            self.assertFalse(_is_command_sequence(node))

    def test_subprocess_env_literal_requirement(self) -> None:
        allowed = ["{\"A\": \"B\", \"C\": \"D\"}", "env", "_filtered_environment()"]
        blocked = ["{\"A\": b, \"C\": os.environ['C']}", "dict(a='b')", "{k: v for k, v in vars.items()}"]

        for expr in allowed:
            node = ast.parse(expr, mode="eval").body
            self.assertTrue(_is_safe_env_argument(node))
        for expr in blocked:
            node = ast.parse(expr, mode="eval").body
            self.assertFalse(_is_safe_env_argument(node))

    def test_runtime_code_does_not_use_os_environ_get(self) -> None:
        src_root = REPO_ROOT / "src"
        offenders: list[str] = []
        for path in src_root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            if "os.environ.get(" in text:
                offenders.append(str(path))
        self.assertEqual(offenders, [], f"os.environ.get should not be used in runtime code: {offenders}")

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
        self.assertIn("uses: actions/upload-artifact@v7", workflow)
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
        self.assertIn("uses: actions/checkout@v6", workflow)
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
        self.assertIn("python -m pip install --disable-pip-version-check --no-cache-dir 'bandit[sarif]'", codacy_workflow)
        self.assertIn("python -m bandit -q -r src/speed_of_cinnamon -x tests -f sarif -o results.sarif --exit-zero", codacy_workflow)
        self.assertIn("uses: github/codeql-action/upload-sarif@v4", codacy_workflow)
        self.assertNotIn("codacy/codacy-analysis-cli-action", codacy_workflow)
        self.assertNotIn("ubuntu-latest", codacy_workflow)

        self.assertIn("name: Security Scan", security_workflow)
        self.assertIn("workflow_call:", security_workflow)
        self.assertIn("workflow_dispatch:", security_workflow)
        self.assertIn("timeout-minutes: 15", security_workflow)
        self.assertIn("timeout-minutes: 10", security_workflow)
        self.assertIn("python-security:", security_workflow)
        self.assertIn("shell-security:", security_workflow)
        self.assertIn("run: python -m pip install --disable-pip-version-check --no-cache-dir bandit", security_workflow)
        self.assertIn("run: make python-security-scan", security_workflow)
        self.assertIn("run: make shell-security-scan", security_workflow)
        self.assertIn("run: |\n          sudo apt-get update\n          sudo apt-get install -y --no-install-recommends shellcheck", security_workflow)
        self.assertIn("permissions:", security_workflow)
        self.assertIn("contents: read", security_workflow)
        self.assertNotIn("contents: write", security_workflow)

    def test_release_publish_does_not_clobber_existing_assets(self) -> None:
        publish_script = (REPO_ROOT / "scripts" / "publish-github-release.sh").read_text(encoding="utf-8")
        self.assertIn("gh release upload", publish_script)
        self.assertNotIn("--clobber", publish_script)
        self.assertIn("--json assets", publish_script)
        self.assertIn(".assets[].name", publish_script)
        self.assertIn("release asset already exists", publish_script)

    def test_wiki_publish_does_not_bootstrap_after_clone_failure(self) -> None:
        publish_script = (REPO_ROOT / "scripts" / "publish-wiki.sh").read_text(encoding="utf-8")
        self.assertIn("failed to clone wiki repository", publish_script)
        self.assertNotIn("git -C \"${work_dir}/wiki\" init", publish_script)

    def test_frogbot_pr_scan_does_not_use_pull_request_target(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "frogbot-scan-pr.yml").read_text(encoding="utf-8")
        self.assertIn("pull_request:", workflow)
        self.assertNotIn("pull_request_target:", workflow)
        self.assertIn("ref: ${{ github.event.pull_request.head.sha }}", workflow)

    def test_authorship_guard_is_part_of_check_target(self) -> None:
        makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        verifier = (REPO_ROOT / "scripts" / "verify-authorship.sh").read_text(encoding="utf-8")

        self.assertIn("check: test lint verify-authorship smoke-doctor security-scan", makefile)
        self.assertIn("coverage:", makefile)
        self.assertIn("coverage run --source=src/speed_of_cinnamon", makefile)
        self.assertIn("coverage lcov -o reports/lcov.info", makefile)
        self.assertIn("PYTHON := $(shell command -v python3", makefile)
        self.assertIn("ifneq ($(strip $(PYTHON)),)", makefile)
        self.assertIn("override PYTHON := $(PYTHON)", makefile)
        self.assertIn("$(error python3 is required)", makefile)
        self.assertIn("verify-authorship:\n\t./scripts/verify-authorship.sh", makefile)
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

    def test_man_pages_and_wiki_are_packaged(self) -> None:
        spec = (REPO_ROOT / "packaging" / "speed-of-cinnamon.spec").read_text(encoding="utf-8")
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
        self.assertIn("%{_mandir}/man1/speed-of-cinnamon.1*", spec)
        self.assertIn("%{_mandir}/man1/speed-of-cinnamon-alarms.1*", spec)
        self.assertIn("speed-of-cinnamon\\.1(\\.gz)?", rpm_verifier)
        self.assertIn("speed-of-cinnamon-alarms\\.1(\\.gz)?", rpm_verifier)
        self.assertIn("docs/man/speed-of-cinnamon.1", install_local)
        self.assertIn('printf \'export PYTHONPATH=%q\\n\' "${app_data}/python"', install_local)
        self.assertIn('SPEED_OF_CINNAMON_TEST_HOME:-0', install_local)
        self.assertIn("reject_unsafe_tree()", install_local)
        self.assertIn('find "${tree}" \\( -type l -o -type f -links +1 \\) -print -quit', install_local)
        self.assertIn("reject_unsafe_file()", install_local)
        self.assertIn('rm -f "${bin_dir}/speed-of-cinnamon"', install_local)
        self.assertIn("python3 -m compileall -q", dist_verifier)
        self.assertIn('exec "$(command -v -- python3)" -m speed_of_cinnamon.cli "$@"', dist_verifier)
        self.assertIn("RPM package contains unsafe path entry", rpm_verifier)
        self.assertIn("RPM expansion contains unsupported symlink entries.", rpm_verifier)
        self.assertIn("RPM expansion contains unsupported hardlink entries.", rpm_verifier)
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
        self.assertIn('snapcraft_file="${snap_dir}/snapcraft.yaml"', build_snap)
        self.assertIn('mv -f -- "${snapcraft_backup}" "${snapcraft_file}"', build_snap)
        self.assertIn("NamedTemporaryFile", build_snap)
        self.assertIn('refusing to overwrite existing snap artifact for version', build_snap)
        self.assertIn('if find "${dist_dir}" "${repo_dir}" -maxdepth 1 -type f -name "speed-of-cinnamon_${version}_*.snap" -print -quit | grep -q .; then', build_snap)
        self.assertNotIn('rm -f -- "${dist_dir}/speed-of-cinnamon_${version}"_*.snap', build_snap)
        self.assertNotIn('path.with_name(path.name + ".tmp")', build_snap)
        self.assertIn('snap_dir="${repo_dir}/dist/snap"', verify_snap)
        self.assertIn('if [[ -L "${snap_dir}" ]]; then', verify_snap)
        self.assertIn('snap directory must not be a symlink', verify_snap)
        self.assertIn("snap file must not be hardlinked", verify_snap)
        self.assertIn('$\'\\n\'', verify_snap)
        self.assertIn('snap file path contains control characters', verify_snap)

    def test_tag_release_workflow_publishes_verified_assets(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        publisher = (REPO_ROOT / "scripts" / "publish-github-release.sh").read_text(encoding="utf-8")

        self.assertIn('name: Release', workflow)
        self.assertIn('- "v*"', workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("run_workflow_lint:", workflow)
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn("publish:\n    needs:\n      - workflow-lint\n      - security-scan", workflow)
        self.assertIn(
            "publish:\n    needs:\n      - workflow-lint\n      - security-scan\n    permissions:\n      contents: write",
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
        self.assertIn("security-scan:", workflow)
        self.assertIn("uses: ./.github/workflows/security-scan.yml", workflow)
        self.assertIn("needs:\n      - workflow-lint\n      - security-scan", workflow)
        self.assertIn('ACTIONLINT_STRICT: "true"', workflow)
        self.assertIn("fetch-depth: 0", workflow)
        self.assertIn("run: gh --version", workflow)
        self.assertIn("uses: actions/checkout@v6", workflow)
        self.assertIn("uses: actions/setup-python@v6", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("uses: actions/upload-artifact@v7", workflow)
        self.assertIn("runs-on: ubuntu-24.04", workflow)
        self.assertNotIn("runs-on: ubuntu-latest", workflow)
        self.assertIn("- name: Install release tooling", workflow)
        self.assertIn("run: |", workflow)
        self.assertIn("sudo apt-get update", workflow)
        self.assertIn("python -m pip install --disable-pip-version-check --no-cache-dir bandit", workflow)
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
        self.assertIn("required_tools=(git python3 realpath awk sha256sum grep)", publisher)
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
        self.assertIn('checksum_dir="$(dirname "${checksums[0]}")"', publisher)
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
        checkout_block = _workflow_block_lines(workflow, "- uses: actions/checkout@v6")

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

    def test_bandit_workflow_does_not_pass_github_token_to_third_party_action(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "bandit.yml").read_text(encoding="utf-8")
        self.assertIn("security-events: write", workflow)
        self.assertNotIn("GITHUB_TOKEN:", workflow)
        self.assertNotIn("secrets.GITHUB_TOKEN", workflow)

    def test_workflows_install_pinned_actionlint_release(self) -> None:
        ci_workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        lint_workflow = (REPO_ROOT / ".github" / "workflows" / "super-linter.yml").read_text(encoding="utf-8")
        linter = (REPO_ROOT / "scripts" / "lint-workflows.sh").read_text(encoding="utf-8")
        self.assertIn('version="1.7.12"', ci_workflow)
        self.assertIn('version="1.7.12"', lint_workflow)
        self.assertIn("rhysd/actionlint/releases/download/v${version}", ci_workflow)
        self.assertIn("rhysd/actionlint/releases/download/v${version}", lint_workflow)
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
        self.assertIn('stage_topdir="$(mktemp -d "${dist_dir}/.$(basename "${final_topdir}").stage.XXXXXX")"', build_rpm)
        self.assertIn('--define "_topdir ${stage_topdir}"', build_rpm)
        self.assertIn('mv -T -- "${stage_topdir}" "${final_topdir}"', build_rpm)
        self.assertNotIn('rm -rf "${topdir}"', build_rpm)

    def test_parallel_build_dist_does_not_corrupt_archive(self) -> None:
        build_dist = REPO_ROOT / "scripts" / "build-dist.sh"
        build_dist_source = build_dist.read_text(encoding="utf-8")
        self.assertIn('staging_checksum="$(mktemp "${dist_dir}/.${package}.tar.gz.sha256.XXXXXX")"', build_dist_source)
        self.assertIn('printf \'%s  %s\\n\' "${checksum_value}" "${package}.tar.gz" > "${staging_checksum}"', build_dist_source)
        self.assertIn('mv -T -- "${staging_checksum}" "${final_checksum}"', build_dist_source)
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
