from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SMOKE_BACKEND = REPO_ROOT / "scripts" / "smoke-backend.sh"


class SmokeBackendStaticTest(unittest.TestCase):
    def test_smoke_backend_validates_tmpdir_before_mktemp(self) -> None:
        source = SMOKE_BACKEND.read_text(encoding="utf-8")

        self.assertIn('readonly TRUSTED_COMMAND_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"', source)
        self.assertIn('export PATH="${TRUSTED_COMMAND_PATH}"', source)
        self.assertIn("resolve_smoke_tmp_root() {", source)
        self.assertIn('temporary root must be an absolute path: %s\\n', source)
        self.assertIn('temporary root must not be a symlink: %s\\n', source)
        self.assertIn('temporary root is not a writable directory: %s\\n', source)
        self.assertIn('safe_fs_cmd=(python3 "${safe_fs}")', source)
        self.assertIn('smoke_tmp_root="$(resolve_smoke_tmp_root)"', source)
        self.assertIn('smoke_root="$(mktemp -d "${smoke_tmp_root}/speed-of-cinnamon-smoke-XXXXXX")"', source)
        self.assertIn('smoke_root_identity=""', source)
        self.assertIn('smoke_root_abs="$(realpath "${smoke_root}")', source)
        self.assertIn('temporary smoke directory escaped temporary root', source)
        self.assertIn('"${safe_fs_cmd[@]}" remove smoke-backend "${smoke_root}" --kind dir', source)
        self.assertIn('--expected-identity "${smoke_root_identity}"', source)
        self.assertIn('refusing smoke cleanup without verified identity', source)
        self.assertNotIn('mktemp -d "${TMPDIR:-/tmp}/speed-of-cinnamon-smoke-XXXXXX"', source)
        self.assertNotIn('rm -rf -- "${smoke_root}"', source)

    def test_smoke_backend_asserts_transcript_payload(self) -> None:
        source = SMOKE_BACKEND.read_text(encoding="utf-8")

        self.assertIn("assert_smoke_transcript() {", source)
        self.assertIn("MAX_SMOKE_JSON_BYTES = 1 * 1024 * 1024", source)
        self.assertIn("sys.stdin.buffer.read(MAX_SMOKE_JSON_BYTES + 1)", source)
        self.assertIn('if len(raw) > MAX_SMOKE_JSON_BYTES:', source)
        self.assertIn('object_pairs_hook=reject_duplicate_keys', source)
        self.assertIn('parse_constant=reject_constant', source)
        self.assertNotIn("json.load(sys.stdin)", source)
        self.assertIn('payload.get("status") != "done"', source)
        self.assertIn('payload.get("transcript") != expected', source)
        self.assertIn('payload.get("transcript_output_redacted")', source)
        self.assertIn('--confirm-plaintext-output', source)
        self.assertIn('assert_smoke_transcript "${stop_output}" "speed-of-cinnamon-smoke"', source)
        self.assertIn('assert_smoke_transcript "${toggle_output}" "speed-of-cinnamon-expired-smoke"', source)

    def test_smoke_backend_bounds_all_backend_output(self) -> None:
        source = SMOKE_BACKEND.read_text(encoding="utf-8")

        self.assertIn("readonly MAX_SMOKE_OUTPUT_BYTES=$((1 * 1024 * 1024))", source)
        self.assertIn("readonly MAX_SMOKE_RUNTIME_SECONDS=30", source)
        self.assertIn("run_backend_bounded() {", source)
        self.assertIn("subprocess.PIPE", source)
        self.assertIn("start_new_session=True", source)
        self.assertIn("selectors.DefaultSelector()", source)
        self.assertIn("selector = None", source)
        self.assertIn("if selector is not None:", source)
        self.assertIn("deadline = time.monotonic() + timeout_seconds", source)
        self.assertIn("reap_timeout_seconds = 1.0", source)
        self.assertIn("process.wait(timeout=reap_timeout_seconds)", source)
        self.assertIn("process.wait(timeout=max(0.0, deadline - time.monotonic()))", source)
        self.assertIn('print("smoke backend did not exit after output closed", file=sys.stderr)', source)
        self.assertIn('print("smoke backend timed out", file=sys.stderr)', source)
        self.assertIn("if len(captured) > limit:", source)
        self.assertIn("os.killpg(process.pid, signal.SIGKILL)", source)
        self.assertIn("run_backend_bounded doctor --json", source)
        self.assertIn("run_backend_bounded cleanup --keep-transcripts 100", source)
        self.assertNotIn('output="$("${backend}"', source)
        self.assertNotIn('"${backend}" doctor --json', source)


if __name__ == "__main__":
    unittest.main()
