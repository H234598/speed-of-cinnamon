from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RealE2EAcceptanceStaticTest(unittest.TestCase):
    def test_real_e2e_requires_explicit_cost_opt_in_and_disables_insertion(self) -> None:
        script = (ROOT / "scripts" / "real-e2e-acceptance.sh").read_text(encoding="utf-8")
        self.assertIn('SOC_REAL_E2E:-0', script)
        self.assertIn('i.insertMethod=\\"none\\"', script)
        self.assertIn('i.autoRelisten=false', script)
        self.assertIn('run_case true', script)
        self.assertIn('run_case false', script)
        self.assertIn('i.status===\\"done\\"&&i.lastTranscript', script)
        self.assertNotIn('_recordingState', script)

    def test_release_requires_fresh_real_e2e_attestation(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        verifier = (ROOT / "scripts" / "verify-real-e2e-attestation.sh").read_text(encoding="utf-8")
        self.assertIn('release: release-validate-flags release-require-snap verify-real-e2e-attestation', makefile)
        self.assertIn('git_head', verifier)
        self.assertIn('timedelta(hours=24)', verifier)
