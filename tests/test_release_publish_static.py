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
