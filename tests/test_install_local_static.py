import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class InstallLocalStaticTest(unittest.TestCase):
    def test_workspace_cleanup_requires_expected_identity(self):
        source = (REPO_ROOT / "scripts" / "install-local.sh").read_text(encoding="utf-8")

        self.assertIn('staged_workspace_identity=""', source)
        self.assertIn('trap install_exit_cleanup EXIT', source)
        self.assertIn(
            'if ! staged_workspace_identity="$(safe_fs identity install "${staged_workspace}" --kind dir)"; then',
            source,
        )
        self.assertIn('--expected-identity "${staged_workspace_identity}"', source)
        self.assertIn(
            'refusing install cleanup without verified identity',
            source,
        )
        self.assertIn('timeout_command=""', source)
        self.assertIn('timeout_command="$(command -v -- timeout || true)"', source)
        self.assertIn('"${timeout_command}" --signal=TERM --kill-after=2s 10s', source)
        self.assertIn('"${dbus_send_command}" --session --reply-timeout=10000', source)
        self.assertIn('if [[ -n "${dbus_send_command}" && -n "${timeout_command}" ]]; then', source)


if __name__ == "__main__":
    unittest.main()
