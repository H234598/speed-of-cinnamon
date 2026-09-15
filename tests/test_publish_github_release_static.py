import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class PublishGithubReleaseStaticTest(unittest.TestCase):
    def test_gh_operations_are_bounded(self):
        source = (REPO_ROOT / "scripts" / "publish-github-release.sh").read_text(encoding="utf-8")

        self.assertIn('readonly GH_TIMEOUT_SECONDS=120', source)
        self.assertIn('gh_path="$(command -v -- gh)"', source)
        self.assertIn('readonly GH_PATH="${gh_path}"', source)
        self.assertIn('gh() {', source)
        self.assertIn(
            'timeout --signal=TERM --kill-after=5s "${GH_TIMEOUT_SECONDS}s" "${GH_PATH}" "$@"',
            source,
        )

    def test_all_temporary_cleanup_requires_expected_identity(self):
        source = (REPO_ROOT / "scripts" / "publish-github-release.sh").read_text(encoding="utf-8")

        self.assertIn('trap cleanup_notes EXIT', source)
        self.assertIn(
            'if ! staging_dir_identity="$("${safe_fs_cmd[@]}" identity publish "${staging_dir}" --kind dir)"; then',
            source,
        )
        self.assertIn(
            'if ! notes_file_identity="$("${safe_fs_cmd[@]}" identity publish "${notes_file}" --kind file)"; then',
            source,
        )
        self.assertIn(
            'if ! existing_notes_file_identity="$("${safe_fs_cmd[@]}" identity publish "${existing_notes_file}" --kind file)"; then',
            source,
        )
        self.assertIn('--expected-identity "${staging_dir_identity}"', source)
        self.assertIn('--expected-identity "${notes_file_identity}"', source)
        self.assertIn('--expected-identity "${existing_notes_file_identity}"', source)
        self.assertIn('refusing release staging cleanup without verified identity', source)
        self.assertIn('refusing release notes cleanup without verified identity', source)
        self.assertIn('refusing existing release notes cleanup without verified identity', source)

    def test_project_metadata_read_is_bounded(self):
        source = (REPO_ROOT / "scripts" / "publish-github-release.sh").read_text(encoding="utf-8")

        self.assertIn("MAX_PROJECT_METADATA_BYTES = 1 << 20", source)
        self.assertIn("handle.read(MAX_PROJECT_METADATA_BYTES + 1)", source)
        self.assertIn("pyproject.toml project.version is invalid", source)
        self.assertIn("RecursionError, MemoryError", source)


if __name__ == "__main__":
    unittest.main()
