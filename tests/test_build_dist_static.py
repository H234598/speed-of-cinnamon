from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_DIST = REPO_ROOT / "scripts" / "build-dist.sh"
VERIFY_DIST = REPO_ROOT / "scripts" / "verify-dist.sh"


class BuildDistStaticTest(unittest.TestCase):
    def test_build_dist_uses_safe_fs_for_source_copying(self) -> None:
        source = BUILD_DIST.read_text(encoding="utf-8")

        self.assertIn('safe_fs="${repo_dir}/scripts/safe-local-fs.py"', source)
        self.assertIn('python3 "${safe_fs}" install-tree build-dist "${source_path}" "${target_path}"', source)
        self.assertIn("distribution_tree_excludes=", source)
        self.assertIn("for tool in python3 tar sha256sum mktemp find grep git stat realpath;", source)
        self.assertIn('--exclude-name __pycache__', source)
        self.assertIn('"${distribution_tree_excludes[@]}"', source)
        self.assertIn('python3 "${safe_fs}" copy-file build-dist "${source_path}" "${target_path}" 0644', source)
        self.assertNotIn('cp -a "${repo_dir}/${path}" "${work_dir}/${package}/"', source)

    def test_verify_dist_checks_companion_checksum(self) -> None:
        source = VERIFY_DIST.read_text(encoding="utf-8")

        self.assertIn('for tool in realpath stat tar awk mktemp find grep python3 sha256sum;', source)
        self.assertIn('checksum_path="${tarball}.sha256"', source)
        self.assertIn('archive checksum file target does not match archive', source)
        self.assertIn('sha256sum "${tarball}"', source)
        self.assertIn('archive checksum mismatch:', source)

    def test_build_dist_cleanup_requires_expected_identity(self) -> None:
        source = BUILD_DIST.read_text(encoding="utf-8")

        self.assertIn(
            'work_dir_identity="$("${safe_fs_cmd[@]}" identity build-dist "${work_dir}" --kind dir)"',
            source,
        )
        self.assertIn('--expected-identity "${work_dir_identity}"', source)
        self.assertIn('--expected-identity "${staging_tarball_identity}"', source)
        self.assertIn('--expected-identity "${staging_checksum_identity}"', source)
        self.assertIn('--expected-identity "${dist_staging_dir_identity}"', source)
        self.assertIn(
            'cache_identity="$("${safe_fs_cmd[@]}" identity build-dist "${cache_dir}" --kind dir)"',
            source,
        )
        self.assertIn(
            'bytecode_identity="$("${safe_fs_cmd[@]}" identity build-dist "${bytecode_file}" --kind file)"',
            source,
        )

    def test_dist_activation_requires_original_stage_and_empty_destination(self) -> None:
        source = BUILD_DIST.read_text(encoding="utf-8")

        activation_start = source.index('entry["activation_attempted"] = True')
        activation_end = source.index('        except BaseException as exc:', activation_start)
        activation = source[activation_start:activation_end]
        self.assertIn('"staging_fs_identity": _safe_fs_identity(staging_stat)', source)
        self.assertIn('"--expected-src-identity",', activation)
        self.assertIn('entry["staging_fs_identity"]', activation)
        self.assertIn('"--expected-dst-identity",', activation)
        self.assertIn('"missing",', activation)


if __name__ == "__main__":
    unittest.main()
