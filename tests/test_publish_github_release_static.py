import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class PublishGithubReleaseStaticTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
