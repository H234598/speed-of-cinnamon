from __future__ import annotations

import ast
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
VERIFY_AUTHORSHIP = REPO_ROOT / "scripts" / "verify-authorship.sh"
INTEGRATION_COMMIT = "a2b9e0a52cf5f5310ea11a6e7b5e7723dda8d98c"
INTEGRATION_IDENTITY = ("SOC Integration", "soc-integration@localhost")


def _load_identity_policy():
    source = VERIFY_AUTHORSHIP.read_text(encoding="utf-8")
    marker = "from __future__ import annotations\n"
    _, separator, embedded = source.partition(marker)
    if not separator:
        raise AssertionError("authorship verifier Python block is missing")
    embedded, separator, _ = embedded.rpartition("\nPY\n")
    if not separator:
        raise AssertionError("authorship verifier Python block is unterminated")

    tree = ast.parse(marker + embedded)
    required_assignments = {
        "expected_name",
        "expected_email",
        "allowed_history_commit_identities",
    }
    selected_nodes = []
    found_assignments = set()
    found_policy = False
    for node in tree.body:
        if isinstance(node, ast.Assign):
            assigned_names = {
                target.id for target in node.targets if isinstance(target, ast.Name)
            }
            if assigned_names & required_assignments:
                selected_nodes.append(node)
                found_assignments.update(assigned_names & required_assignments)
        elif (
            isinstance(node, ast.FunctionDef)
            and node.name == "_commit_identity_is_allowed"
        ):
            selected_nodes.append(node)
            found_policy = True
    if found_assignments != required_assignments or not found_policy:
        raise AssertionError("authorship identity policy is incomplete")

    namespace = {}
    policy_module = ast.fix_missing_locations(
        ast.Module(body=selected_nodes, type_ignores=[])
    )
    exec(compile(policy_module, "<verify-authorship-policy>", "exec"), namespace)
    return (
        namespace["allowed_history_commit_identities"],
        namespace["_commit_identity_is_allowed"],
    )


class VerifyAuthorshipStaticTest(unittest.TestCase):
    def test_exact_integration_commit_identity_is_allowed(self) -> None:
        allowed_identities, identity_is_allowed = _load_identity_policy()

        self.assertEqual(len(INTEGRATION_COMMIT), 40)
        self.assertEqual(
            allowed_identities[INTEGRATION_COMMIT],
            (INTEGRATION_IDENTITY, INTEGRATION_IDENTITY),
        )
        self.assertTrue(
            identity_is_allowed(
                INTEGRATION_COMMIT,
                *INTEGRATION_IDENTITY,
                *INTEGRATION_IDENTITY,
            )
        )

    def test_integration_commit_rejects_different_identity(self) -> None:
        _, identity_is_allowed = _load_identity_policy()

        for author, committer in (
            (("Other", INTEGRATION_IDENTITY[1]), INTEGRATION_IDENTITY),
            (INTEGRATION_IDENTITY, ("Other", INTEGRATION_IDENTITY[1])),
        ):
            with self.subTest(author=author, committer=committer):
                self.assertFalse(
                    identity_is_allowed(
                        INTEGRATION_COMMIT,
                        *author,
                        *committer,
                    )
                )

    def test_integration_identity_rejects_different_commit(self) -> None:
        _, identity_is_allowed = _load_identity_policy()
        different_commit = "b" + INTEGRATION_COMMIT[1:]

        self.assertEqual(len(different_commit), 40)
        self.assertNotEqual(different_commit, INTEGRATION_COMMIT)
        self.assertFalse(
            identity_is_allowed(
                different_commit,
                *INTEGRATION_IDENTITY,
                *INTEGRATION_IDENTITY,
            )
        )

    def test_git_identity_output_is_deterministic(self) -> None:
        source = VERIFY_AUTHORSHIP.read_text(encoding="utf-8")

        self.assertIn('"--no-color",', source)
        self.assertIn('"--no-show-signature",', source)

    def test_forbidden_scan_is_bounded_and_does_not_follow_special_files(self) -> None:
        source = VERIFY_AUTHORSHIP.read_text(encoding="utf-8")

        self.assertIn("FORBIDDEN_SCAN_CHUNK_BYTES = 1 << 20", source)
        self.assertIn("FORBIDDEN_SCAN_OVERLAP_CHARS = 64", source)
        self.assertIn('getattr(os, "O_NOFOLLOW", 0)', source)
        self.assertIn('getattr(os, "O_NONBLOCK", 0)', source)
        self.assertIn("stat.S_ISREG(os.fstat(fd).st_mode)", source)
        self.assertIn("overlap = candidate[-FORBIDDEN_SCAN_OVERLAP_CHARS:]", source)

    def test_project_metadata_reader_is_bounded_and_no_follow(self) -> None:
        source = VERIFY_AUTHORSHIP.read_text(encoding="utf-8")

        self.assertIn("MAX_PROJECT_METADATA_BYTES = 1 << 20", source)
        self.assertIn('getattr(os, "O_NOFOLLOW", None)', source)
        self.assertIn("os.read(fd, MAX_PROJECT_METADATA_BYTES + 1)", source)
        self.assertIn("project metadata changed while reading", source)
        self.assertIn("object_pairs_hook=reject_duplicate_json_keys", source)
        self.assertIn("parse_constant=reject_non_finite_json_number", source)

    def test_repository_file_enumeration_is_bounded(self) -> None:
        source = VERIFY_AUTHORSHIP.read_text(encoding="utf-8")

        self.assertIn("MAX_TRACKED_ENTRIES = 100_000", source)
        self.assertIn("MAX_TRACKED_FILE_LIST_BYTES = 16 * 1024 * 1024", source)
        self.assertIn("subprocess.Popen(", source)
        self.assertIn("TRACKED_FILE_LIST_CHUNK_BYTES", source)
        self.assertIn('pending.split(b"\\0", 1)', source)
        self.assertIn("tracked file list exceeds byte budget", source)
        self.assertIn("MAX_COMMIT_LOG_BYTES = 16 * 1024 * 1024", source)
        self.assertIn("COMMIT_LOG_CHUNK_BYTES", source)
        self.assertIn("def iter_git_log_records", source)
        self.assertIn('pending.split(b"\\x1e", 1)', source)
        self.assertIn("commit history exceeds byte budget", source)
        self.assertIn("MAX_GIT_SCALAR_OUTPUT_BYTES = 4 * 1024", source)
        self.assertIn("GIT_SCALAR_OUTPUT_CHUNK_BYTES", source)
        self.assertIn("GIT_TIMEOUT_SECONDS = 30.0", source)
        self.assertIn("def iter_git_output_chunks", source)
        self.assertIn("git command timed out", source)
        self.assertIn("selector.select(remaining)", source)
        self.assertIn("git scalar output exceeds byte budget", source)
        self.assertIn("reap_process(process)", source)
        self.assertNotIn("process.wait()", source)
        self.assertIn("selector = None", source)
        self.assertIn("if selector is not None:", source)
        self.assertIn("os.scandir(current_directory)", source)
        self.assertIn("tracked file scan exceeds", source)
        self.assertNotIn('run_git("ls-files", "-z")', source)
        self.assertNotIn("os.walk(", source)


if __name__ == "__main__":
    unittest.main()
