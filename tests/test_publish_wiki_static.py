import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class PublishWikiStaticTest(unittest.TestCase):
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
