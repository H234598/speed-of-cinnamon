from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_SNAP = REPO_ROOT / "scripts" / "build-snap.sh"


class BuildSnapStaticTest(unittest.TestCase):
    def test_build_snap_does_not_delete_unrelated_repository_root_artifacts(self) -> None:
        source = BUILD_SNAP.read_text(encoding="utf-8")

        self.assertNotIn("cleanup_existing_root_snaps", source)
        self.assertNotIn('find "${repo_dir}" -maxdepth 1 -name "speed-of-cinnamon_${version}_*.snap"', source)
        self.assertNotIn('cleanup_existing_dist_snaps "$(basename "${output_path}")"', source)

    def test_build_snap_cleanup_requires_expected_identity(self) -> None:
        source = BUILD_SNAP.read_text(encoding="utf-8")

        self.assertIn(
            'snap_workspace_identity="$("${safe_fs_cmd[@]}" identity build-snap "${snap_workspace}" --kind dir)"',
            source,
        )
        self.assertIn('--expected-identity "${snap_workspace_identity}"', source)
        self.assertIn(
            'tmp_output_identity="$("${safe_fs_cmd[@]}" identity build-snap "${tmp_output}" --kind file)"',
            source,
        )
        self.assertIn('--expected-identity "${tmp_output_identity}"', source)
        self.assertIn(
            'remove build-snap "${snap_workspace_dist}" --kind dir --expected-identity missing',
            source,
        )

    def test_snap_activation_requires_original_stage_and_empty_destination(self) -> None:
        source = BUILD_SNAP.read_text(encoding="utf-8")

        activation_start = source.index('activation_attempted = True')
        activation_end = source.index('        except BaseException as exc:', activation_start)
        activation = source[activation_start:activation_end]
        self.assertIn('"--expected-src-identity",', activation)
        self.assertIn('_safe_fs_identity(staging_stat)', activation)
        self.assertIn('"--expected-dst-identity",', activation)
        self.assertIn('"missing",', activation)


if __name__ == "__main__":
    unittest.main()
