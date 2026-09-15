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

    def test_success_gate_uses_staging_manifests_before_completion(self):
        source = (REPO_ROOT / "scripts" / "install-local.sh").read_text(encoding="utf-8")
        safe_fs_source = (REPO_ROOT / "scripts" / "safe-local-fs.py").read_text(encoding="utf-8")

        self.assertIn("--exclude-name __pycache__", source)
        self.assertNotIn("--create-only", source)
        self.assertNotIn('install_tree.add_argument("--create-only"', safe_fs_source)
        self.assertNotIn("create_only", safe_fs_source)
        self.assertNotIn("backup_name", safe_fs_source)
        self.assertIn("snapshot-tree", source)
        self.assertIn("verify-tree", source)
        self.assertIn("exchange install", source)
        self.assertIn('readonly REQUIRED_TOOLS=(dirname find flock grep getent id mktemp realpath cut python3)', source)
        self.assertIn('if ! exec {install_lock_fd}<"${app_data}"; then', source)
        self.assertIn('if ! "${flock_command}" -n "${install_lock_fd}"; then', source)
        self.assertNotIn('cleanup_stale_install_workspaces', source)
        self.assertIn('safe_fs cleanup-install-stages install "${app_data}"', source)
        self.assertIn('assert_private_chain "${HOME}" "install"', source)
        self.assertIn('safe_fs assert-private-chain', source)
        self.assertIn('--allow-missing', source)
        self.assertIn('set_staged_phase recovery-required', source)
        self.assertNotIn('set_staged_phase safe-complete', source)
        self.assertNotIn('install-stage-*"', source)
        self.assertNotIn('safe-complete', safe_fs_source)
        self.assertIn('cleanup-install-stages', safe_fs_source)
        self.assertIn('MAX_STALE_INSTALL_WORKSPACES = 32', safe_fs_source)
        self.assertIn('INSTALL_PHASE_MAX_BYTES = 64', safe_fs_source)
        self.assertNotIn('safe_fs lock install', source)
        self.assertNotIn('install_lock="${app_data}/.install.lock"', source)
        self.assertIn('readonly MAX_TREE_FILE_BYTES=536870912', source)
        self.assertIn('--max-bytes "${MAX_TREE_FILE_BYTES}"', source)
        self.assertIn("staging_applet_digest", source)
        self.assertIn("staging_python_digest", source)
        self.assertIn("staging_wrapper_digest", source)
        self.assertIn("staging_man_digest", source)
        self.assertIn("staging_alarms_digest", source)
        self.assertIn("verify_installed_targets\ninstall_complete=1", source)
        self.assertNotIn("--version", source)
        self.assertNotIn("_tree_signature", source)
        self.assertLess(source.index('if ! "${flock_command}" -n "${install_lock_fd}"; then'), source.index("staging_applet_digest=\"$(snapshot_staging_target"))
        self.assertLess(source.index('staging_applet_digest="$(snapshot_staging_target'), source.index('activate_staged "'))

    def test_checked_hash_bytecode_precedes_manifest_and_activation(self):
        source = (REPO_ROOT / "scripts" / "install-local.sh").read_text(encoding="utf-8")

        self.assertEqual(source.count("--exclude-name __pycache__"), 1)
        self.assertIn('"${python3_path}" -I -m compileall -q -f', source)
        self.assertIn("--invalidation-mode checked-hash", source)
        self.assertIn('-s "${stage_python_root}" -p "${app_data}/python"', source)
        self.assertIn('safe_fs write-wrapper install', source)
        self.assertIn('"${app_data}/python" "${python3_path}"', source)
        self.assertIn("importlib.util.MAGIC_NUMBER", source)
        self.assertIn('int.from_bytes(header[4:8], "little") != 3', source)
        self.assertIn("importlib.util.source_hash(source_bytes)", source)
        self.assertIn("info.st_nlink != 1", source)
        self.assertIn("mode != 0o600", source)
        self.assertIn("cache_directories != expected_cache_directories", source)
        self.assertNotIn('"python package" \\\n  --exclude-name __pycache__', source)
        copy_index = source.index('safe_fs install-tree install "${source_root}/src/speed_of_cinnamon"')
        compile_index = source.index('compile_staged_python "${stage_root}/speed-of-cinnamon/python"')
        manifest_index = source.index('staging_python_digest="$(snapshot_staging_target')
        activation_index = source.index(
            'activate_staged "${staging_root}/speed-of-cinnamon/python/speed_of_cinnamon"'
        )
        self.assertLess(copy_index, compile_index)
        self.assertLess(compile_index, manifest_index)
        self.assertLess(manifest_index, activation_index)


if __name__ == "__main__":
    unittest.main()
