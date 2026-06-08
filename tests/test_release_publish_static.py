from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_new_release_rollback_state_is_set_only_after_create_success():
    script = (REPO_ROOT / "scripts" / "publish-github-release.sh").read_text(
        encoding="utf-8"
    )

    release_block = script[
        script.index('if gh release view "${tag}"') : script.index(
            'if ! gh release upload "${tag}"'
        )
    ]
    create_call = release_block.index('gh release create "${tag}"')
    created_flag = release_block.index('created_release="true"', create_call)
    mutation_flag = release_block.index("mark_release_mutation", created_flag)

    assert create_call < created_flag < mutation_flag


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
    assert 'commit="${GITHUB_SHA:-${tag_commit}}"' in script
    assert 'if [[ "${commit}" != "${tag_commit}" ]]; then' in script
    assert "GITHUB_SHA does not match release tag commit" in script
