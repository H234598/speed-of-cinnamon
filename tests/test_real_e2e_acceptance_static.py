from pathlib import Path
import ast
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RealE2EAcceptanceStaticTest(unittest.TestCase):
    def test_attestation_verifiers_embedded_python_is_syntax_valid(self) -> None:
        for name in ("verify-real-e2e-attestation.sh", "verify-local-model-e2e-attestation.sh"):
            script = (ROOT / "scripts" / name).read_text(encoding="utf-8")
            marker = "<<'PY'\n"
            start = script.index(marker) + len(marker)
            end = script.index("\nPY", start)
            ast.parse(script[start:end], filename=name)

    def test_real_e2e_requires_explicit_cost_opt_in_and_disables_insertion(self) -> None:
        script = (ROOT / "scripts" / "real-e2e-acceptance.sh").read_text(encoding="utf-8")
        self.assertIn('SOC_REAL_E2E:-0', script)
        self.assertIn('for tool in arecord espeak-ng ffmpeg gdbus pactl pw-play python3 mktemp git timeout; do', script)
        self.assertIn('timeout --signal=TERM --kill-after=2s 10s', script)
        self.assertIn('i.insertMethod=\\"none\\"', script)
        self.assertIn('i.autoRelisten=false', script)
        self.assertIn('i._socRealE2eSnapshot={insertMethod:i.insertMethod', script)
        self.assertIn('inputDevice:i.inputDevice', script)
        self.assertIn('i.recorder===\\\"arecord\\\"', script)
        self.assertIn('i.inputDevice=\\\"pipewire\\\"', script)
        self.assertIn('snapshot_set=1\neval_cinnamon', script)
        self.assertIn('run_case true', script)
        self.assertIn('run_case false', script)
        self.assertIn('i&&i.status===\\"recording\\"', script)
        self.assertIn('i.status===\\"done\\"&&i.lastTranscript', script)
        self.assertNotIn('_recordingState', script)

    def test_release_requires_fresh_real_e2e_attestation(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        verifier = (ROOT / "scripts" / "verify-real-e2e-attestation.sh").read_text(encoding="utf-8")
        self.assertIn('release: check release-validate-flags release-require-snap verify-real-e2e-attestation verify-local-model-e2e-attestation', makefile)
        self.assertIn('release-dry-run: check release-validate-flags release-require-snap verify-real-e2e-attestation verify-local-model-e2e-attestation', makefile)
        self.assertIn('release-dry-run-no-snap: check release-validate-flags verify-real-e2e-attestation verify-local-model-e2e-attestation', makefile)
        self.assertIn('git_head', verifier)
        self.assertIn('timedelta(hours=24)', verifier)

    def test_attestation_schema_version_requires_a_real_integer(self) -> None:
        for name in ("verify-real-e2e-attestation.sh", "verify-local-model-e2e-attestation.sh"):
            verifier = (ROOT / "scripts" / name).read_text(encoding="utf-8")
            self.assertIn('isinstance(schema_version, bool)', verifier)
            self.assertIn('not isinstance(schema_version, int)', verifier)
            self.assertIn('if not isinstance(data, dict):', verifier)
            self.assertIn('contains unexpected fields', verifier)

    def test_attestation_verifiers_open_checked_descriptors(self) -> None:
        for name in ("verify-real-e2e-attestation.sh", "verify-local-model-e2e-attestation.sh"):
            verifier = (ROOT / "scripts" / name).read_text(encoding="utf-8")
            self.assertIn('getattr(os, "O_NOFOLLOW", None)', verifier)
            self.assertIn('os.fstat(fd)', verifier)
            self.assertIn('os.fdopen(fd', verifier)
            self.assertIn("descriptor cleanup failed", verifier)
            self.assertIn('MAX_ATTESTATION_BYTES = 4 * 1024 * 1024', verifier)
            self.assertIn('handle.read(MAX_ATTESTATION_BYTES + 1)', verifier)
            self.assertIn('entry.st_size > MAX_ATTESTATION_BYTES', verifier)
            self.assertIn('object_pairs_hook=unique_object', verifier)
            self.assertIn('parse_constant=reject_constant', verifier)

    def test_authorship_verifier_reports_descriptor_cleanup_failure(self) -> None:
        verifier = (ROOT / "scripts" / "verify-authorship.sh").read_text(encoding="utf-8")
        self.assertIn("authorship scan descriptor cleanup failed", verifier)
        self.assertIn("primary_error.add_note", verifier)

    def test_local_model_e2e_requires_all_installed_local_backends_and_safe_output(self) -> None:
        script_path = ROOT / "scripts" / "local-model-e2e-acceptance.sh"
        self.assertTrue(script_path.is_file())
        script = script_path.read_text(encoding="utf-8")
        verifier = (ROOT / "scripts" / "verify-local-model-e2e-attestation.sh").read_text(encoding="utf-8")
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn('"faster-whisper"', script)
        self.assertIn('"whisper-cpp"', script)
        self.assertIn('--transcriber', script)
        self.assertIn('transcribe-file', script)
        self.assertIn('entry.get("verified") is not True', script)
        self.assertIn('installed model integrity not verified', script)
        self.assertIn('model_attestation_snapshot', script)
        self.assertIn('model_attestation_snapshot', verifier)
        self.assertIn('attestation model artifacts changed', verifier)
        self.assertIn('source_attestation_snapshot', script)
        self.assertIn('source_attestation_snapshot', verifier)
        self.assertIn('attestation source changed', verifier)
        self.assertIn('MAX_LOCAL_MODEL_JSON_BYTES = 4 * 1024 * 1024', script)
        self.assertIn('handle.read(MAX_LOCAL_MODEL_JSON_BYTES + 1)', script)
        self.assertIn('MAX_ATTESTATION_BYTES = 4 * 1024 * 1024', verifier)
        self.assertIn('handle.read(MAX_ATTESTATION_BYTES + 1)', verifier)
        self.assertIn('entry.st_size > MAX_ATTESTATION_BYTES', verifier)
        self.assertNotIn('--insert-method', script)
        self.assertIn('verify-local-model-e2e-attestation', makefile)

    def test_local_model_e2e_is_hugging_face_offline_and_does_not_forward_tokens(self) -> None:
        script = (ROOT / "scripts" / "local-model-e2e-acceptance.sh").read_text(encoding="utf-8")
        self.assertIn('export HF_HUB_OFFLINE=1', script)
        self.assertIn('export HF_DATASETS_OFFLINE=1', script)
        self.assertIn('export TRANSFORMERS_OFFLINE=1', script)
        self.assertIn('export HF_HUB_DISABLE_TELEMETRY=1', script)
        self.assertIn('export HF_HUB_DISABLE_IMPLICIT_TOKEN=1', script)
        self.assertIn('unset HF_TOKEN HUGGINGFACE_HUB_TOKEN', script)
