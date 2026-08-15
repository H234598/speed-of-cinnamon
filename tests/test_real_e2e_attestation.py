from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]


class RealE2EAttestationStaticTests(unittest.TestCase):
    def test_acceptance_binds_attestation_to_source_snapshot(self) -> None:
        script = (REPO_ROOT / "scripts" / "real-e2e-acceptance.sh").read_text(encoding="utf-8")
        self.assertIn('from speed_of_cinnamon.models import ModelError, source_attestation_snapshot', script)
        self.assertIn('source = source_attestation_snapshot(Path(repo_dir))', script)
        self.assertIn('"source": source', script)
        self.assertIn('os.fsync(handle.fileno())', script)

    def test_verifier_rejects_changed_source_snapshot(self) -> None:
        script = (REPO_ROOT / "scripts" / "verify-real-e2e-attestation.sh").read_text(encoding="utf-8")
        self.assertIn('PYTHONPATH="${repo_dir}/src"', script)
        self.assertIn('attested_source = data.get("source")', script)
        self.assertIn('source_attestation_snapshot(Path(repo_dir))', script)
        self.assertIn('real-e2e attestation source changed; rerun acceptance', script)


if __name__ == "__main__":
    unittest.main()
