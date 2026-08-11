from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_new_release_rollback_state_is_set_only_after_create_success():
    script = (REPO_ROOT / "scripts" / "publish-github-release.sh").read_text(
        encoding="utf-8"
    )

    release_block = script[
        script.index('release_probe_status=') : script.index(
            'for upload_ref in "${upload_refs[@]}"'
        )
    ]
    assert 'gh api --include --silent "repos/${repo}/releases/tags/${tag}"' in release_block
    assert 'if [[ "${release_probe_status}" == "200" ]]; then' in release_block
    assert 'elif [[ "${release_probe_status}" == "404" ]]; then' in release_block
    assert 'could not determine release state' in release_block

    create_call = release_block.index('gh release create "${tag}"')
    created_flag = release_block.index('created_release="true"', create_call)
    mutation_flag = release_block.index("mark_release_mutation", created_flag)

    assert create_call < created_flag < mutation_flag


def test_remote_parser_accepts_allowed_ssh_url_form():
    script = (REPO_ROOT / "scripts" / "publish-github-release.sh").read_text(
        encoding="utf-8"
    )

    assert 'ssh://git@github\\.com/([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)' in script


def test_release_rollback_tracks_only_successful_uploads():
    script = (REPO_ROOT / "scripts" / "publish-github-release.sh").read_text(
        encoding="utf-8"
    )

    staging_end = script.index('if [[ -z "${source_archive_ref}"')
    staging_block = script[:staging_end]
    upload_start = script.index('for upload_ref in "${upload_refs[@]}"')
    upload_end = script.index('if ! gh release edit "${tag}"', upload_start)
    upload_block = script[upload_start:upload_end]

    assert 'uploaded_asset_names+=("${staged_name}")' not in staging_block
    assert 'if ! gh release upload "${tag}" "${upload_ref}" --repo "${repo}"' in upload_block
    assert upload_block.index('gh release upload') < upload_block.index(
        'uploaded_asset_names+=("$(basename "${upload_ref}")")'
    )


def test_repo_falls_back_to_target_when_github_repository_empty_and_origin_is_allowed():
    script = (REPO_ROOT / "scripts" / "publish-github-release.sh").read_text(
        encoding="utf-8"
    )
    section = script[
        script.index('repo="${GITHUB_REPOSITORY:-}"') : script.index('if ! tag_commit="$(git rev-parse --verify "${tag}^{commit}")"')
    ]
    remote_target_check = 'if [[ -n "${remote_repo}" && "${remote_repo}" != "${RELEASE_TARGET_REPOSITORY}" ]]; then'
    checked_out_mismatch_check = (
        'if [[ -n "${remote_repo}" && "${repo}" != "${remote_repo}" ]]; then'
    )

    remote_target_check = section.index(remote_target_check)
    fallback_assignment = section.index("repo=\"${RELEASE_TARGET_REPOSITORY}\"")
    checked_out_mismatch_check = section.index(checked_out_mismatch_check)

    assert remote_target_check < fallback_assignment < checked_out_mismatch_check


def test_release_notes_commit_must_match_verified_tag_commit():
    script = (REPO_ROOT / "scripts" / "publish-github-release.sh").read_text(
        encoding="utf-8"
    )

    assert 'if ! tag_commit="$(git rev-parse --verify "${tag}^{commit}")"; then' in script
    assert 'commit="${RELEASE_EXPECTED_COMMIT:-${GITHUB_SHA:-${tag_commit}}}"' in script
    assert 'if [[ "${commit}" != "${tag_commit}" ]]; then' in script
    assert "release expected commit does not match release tag commit" in script
    assert "GITHUB_SHA does not match release tag commit" not in script


def test_release_requires_main_or_verified_github_release_ref():
    script = (REPO_ROOT / "scripts" / "publish-github-release.sh").read_text(
        encoding="utf-8"
    )

    assert 'readonly RELEASE_EXPECTED_BRANCH="main"' in script
    assert 'current_branch="$(git symbolic-ref --quiet --short HEAD || true)"' in script
    assert 'if [[ "${current_branch}" != "${RELEASE_EXPECTED_BRANCH}" ]]; then' in script
    assert 'elif [[ "${GITHUB_EVENT_NAME:-}" == "workflow_dispatch" ]]; then' in script
    assert '"${GITHUB_REF_NAME:-}" != "${RELEASE_EXPECTED_BRANCH}"' in script
    assert 'elif [[ "${GITHUB_REF_TYPE:-}" == "tag" ]]; then' in script
    assert '"${GITHUB_REF_NAME:-}" != "${tag}"' in script
    assert "release requires %s branch checkout or verified GitHub release ref" in script
