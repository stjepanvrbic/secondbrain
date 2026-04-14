"""Tests for version automation, docs, and validation hooks."""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
AUTO_BUMP_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "auto-bump-main.yml"
LEGACY_AUTO_RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "auto-release-main.yml"
LEGACY_PUBLISH_RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish-release.yml"
PRE_PUSH = REPO_ROOT / ".githooks" / "pre-push"
README = REPO_ROOT / "README.md"
CONTRIBUTING = REPO_ROOT / "CONTRIBUTING.md"
ENVIRONMENTS = REPO_ROOT / "secondbrain" / "references" / "environments.md"


def test_auto_bump_workflow_exists():
    assert AUTO_BUMP_WORKFLOW.is_file(), (
        "main-branch version bumps must be automated in GitHub Actions"
    )


def test_legacy_release_workflows_are_removed():
    assert not LEGACY_AUTO_RELEASE_WORKFLOW.exists(), (
        "auto-release-main.yml should be replaced by a bump-only workflow"
    )
    assert not LEGACY_PUBLISH_RELEASE_WORKFLOW.exists(), (
        "publish-release.yml should be removed when GitHub releases are no longer authoritative"
    )


def test_auto_bump_workflow_triggers_on_pushes_to_main():
    text = AUTO_BUMP_WORKFLOW.read_text(encoding="utf-8")
    assert "push:" in text
    assert "branches:" in text
    assert "main" in text


def test_auto_bump_workflow_skips_bot_loops_and_pushes_version_commit_only():
    text = AUTO_BUMP_WORKFLOW.read_text(encoding="utf-8")
    assert "github-actions[bot]" in text or "chore: bump version to" in text, (
        "auto-bump must skip the follow-up push it creates itself"
    )
    assert "auto_release.py" in text, (
        "workflow must use the shared auto_release.py helper rather than duplicating bump logic inline"
    )
    assert "bump_version.py" in text, (
        "workflow must rewrite version-managed files through bump_version.py"
    )
    assert "git commit -m \"chore: bump version to" in text, (
        "workflow must write a single, machine-detectable version bump commit"
    )
    assert "HEAD:main" in text, "auto-bump must push the version commit back to main"
    assert "gh release" not in text, (
        "auto-bump workflow must not create or upload GitHub releases"
    )
    assert "git tag" not in text, (
        "auto-bump workflow must not create tags when GitHub marketplace is authoritative"
    )
    assert "validate_distribution.py" in text, (
        "auto-bump must still run marketplace/distribution validation"
    )


def test_pre_push_is_validation_only():
    text = PRE_PUSH.read_text(encoding="utf-8")
    assert "validate_distribution.py" in text, (
        "pre-push must validate marketplace layout and the local Claude install smoke path"
    )
    assert "--claude-smoke" in text, (
        "pre-push must still run the local Claude marketplace/install smoke test"
    )
    assert "release.json" not in text, (
        "pre-push must not mention removed release-manifest files"
    )
    assert "--release" not in text and "--tag" not in text, (
        "pre-push must not create commits or tags; automation belongs in GitHub Actions"
    )
    assert "GitHub Releases" not in text and "release ZIP" not in text, (
        "pre-push must not encode the old release-asset distribution model"
    )


def test_pre_push_skips_deletion_only_pushes():
    text = PRE_PUSH.read_text(encoding="utf-8")
    assert "while read -r local_ref local_sha remote_ref remote_sha" in text, (
        "pre-push must inspect the refs git passes on stdin so it can distinguish "
        "real updates from deletion-only pushes"
    )
    assert "deletion-only push detected" in text, (
        "pre-push must print an explicit skip message for deletion-only pushes "
        "instead of running the full validation gate"
    )
    assert "0000000000000000000000000000000000000000" in text, (
        "pre-push must recognize Git's all-zero object id used for deletion refs"
    )


def test_pre_push_only_skips_when_every_ref_is_a_delete():
    text = PRE_PUSH.read_text(encoding="utf-8")
    assert "deletion_only=1" in text, (
        "pre-push must track whether all pushed refs are deletions before deciding to skip"
    )
    assert 'if [ "$deletion_only" -eq 1 ]; then' in text, (
        "pre-push must gate the skip path on every ref being a deletion"
    )
    assert "validation gate starting" in text, (
        "pre-push must continue into the normal validation flow for mixed or regular pushes"
    )


def test_docs_use_github_marketplace_flow_and_not_uploads():
    readme = README.read_text(encoding="utf-8")
    contributing = CONTRIBUTING.read_text(encoding="utf-8")
    environments = ENVIRONMENTS.read_text(encoding="utf-8")

    assert "/plugin marketplace add stjepanvrbic/secondbrain" in readme
    assert "/plugin install secondbrain@secondbrain" in readme
    assert "Add marketplace from GitHub" in readme

    forbidden = [
        "stjepanvrbic-secondbrain",
        "secondbrain-vX.Y.Z.zip",
        "GitHub Releases",
        "manual ZIP upload",
        "Cowork doesn't support direct GitHub install",
        "Upload ZIP or marketplace",
        "My Uploads",
    ]

    for needle in forbidden:
        assert needle not in readme, f"README still contains legacy distribution guidance: {needle!r}"
        assert needle not in contributing, f"CONTRIBUTING still contains legacy distribution guidance: {needle!r}"
        assert needle not in environments, f"environments.md still contains legacy distribution guidance: {needle!r}"
