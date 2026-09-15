import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class PublishWikiStaticTest(unittest.TestCase):
    def test_git_operations_are_bounded(self):
        source = (REPO_ROOT / "scripts" / "publish-wiki.sh").read_text(encoding="utf-8")

        self.assertIn('readonly GIT_TIMEOUT_SECONDS=120', source)
        self.assertIn('for tool in git timeout python3 stat command realpath; do', source)
        self.assertIn('run_git_bounded() {', source)
        self.assertIn(
            'timeout --signal=TERM --kill-after=5s "${GIT_TIMEOUT_SECONDS}s" git "$@"',
            source,
        )
        for command in ("clone", "symbolic-ref", "status", "add", "commit", "push"):
            self.assertIn(f"run_git_bounded {command}", source)
        self.assertIn('if ! wiki_status="$(run_git_bounded status --porcelain -- .)"; then', source)
        self.assertIn("failed to inspect wiki working tree.", source)

    def test_cleanup_requires_expected_identity(self):
        source = (REPO_ROOT / "scripts" / "publish-wiki.sh").read_text(encoding="utf-8")

        self.assertIn('work_dir_identity=""', source)
        self.assertIn('trap cleanup EXIT', source)
        self.assertIn(
            'if ! work_dir_identity="$("${safe_fs_cmd[@]}" identity publish-wiki "${work_dir}" --kind dir)"; then',
            source,
        )
        self.assertIn('--expected-identity "${work_dir_identity}"', source)
        self.assertIn(
            'refusing wiki publish cleanup without verified identity',
            source,
        )


if __name__ == "__main__":
    unittest.main()
