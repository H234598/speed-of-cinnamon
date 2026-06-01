from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class CiStaticTest(unittest.TestCase):
    def test_ci_uploads_release_and_rpm_artifacts(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

        self.assertIn("fetch-depth: 0", workflow)
        self.assertIn("run: make check", workflow)
        self.assertIn("sudo apt-get update && sudo apt-get install -y cpio rpm shellcheck", workflow)
        self.assertIn("run: make rpm-check", workflow)
        self.assertIn("uses: actions/upload-artifact@v7.0.1", workflow)
        self.assertIn("name: speed-of-cinnamon-source-${{ github.sha }}", workflow)
        self.assertIn("dist/speed-of-cinnamon-*.tar.gz", workflow)
        self.assertIn("dist/speed-of-cinnamon-*.tar.gz.sha256", workflow)
        self.assertIn("name: speed-of-cinnamon-rpm-${{ github.sha }}", workflow)
        self.assertIn("dist/rpmbuild/RPMS/**/*.rpm", workflow)
        self.assertIn("dist/rpmbuild/SRPMS/**/*.rpm", workflow)
        self.assertEqual(workflow.count("if-no-files-found: error"), 2)

    def test_authorship_guard_is_part_of_check_target(self) -> None:
        makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        verifier = (REPO_ROOT / "scripts" / "verify-authorship.sh").read_text(encoding="utf-8")

        self.assertIn("check: test lint verify-authorship smoke-doctor", makefile)
        self.assertIn("verify-authorship:\n\t./scripts/verify-authorship.sh", makefile)
        self.assertIn('expected_name = "H234598"', verifier)
        self.assertIn('expected_email = "54270221+H234598@users.noreply.github.com"', verifier)
        self.assertIn('expected_repo = "github.com/H234598/speed-of-cinnamon"', verifier)

    def test_tag_release_workflow_publishes_verified_assets(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        publisher = (REPO_ROOT / "scripts" / "publish-github-release.sh").read_text(encoding="utf-8")

        self.assertIn('name: Release', workflow)
        self.assertIn('- "v*"', workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("dry_run:", workflow)
        self.assertIn("contents: write", workflow)
        self.assertIn("fetch-depth: 0", workflow)
        self.assertIn("run: gh --version", workflow)
        self.assertIn("run: make check", workflow)
        self.assertIn("run: make dist-check", workflow)
        self.assertIn("run: make rpm", workflow)
        self.assertIn("run: make rpm-check", workflow)
        self.assertIn("run: shellcheck scripts/*.sh", workflow)
        self.assertIn("GH_TOKEN: ${{ github.token }}", workflow)
        self.assertIn("RELEASE_TAG:", workflow)
        self.assertIn("RELEASE_DRY_RUN:", workflow)
        self.assertIn("args+=(--dry-run)", workflow)
        self.assertIn('./scripts/publish-github-release.sh "${args[@]}" "${RELEASE_TAG}"', workflow)
        self.assertIn('expected_tag="v${version}"', publisher)
        self.assertIn("gh release create", publisher)
        self.assertIn("gh release upload", publisher)
        self.assertIn("--clobber", publisher)
