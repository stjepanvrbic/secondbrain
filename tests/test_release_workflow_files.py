"""Tests for release automation workflow files and local hook behavior."""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
AUTO_RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "auto-release-main.yml"
PUBLISH_RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish-release.yml"
PRE_PUSH = REPO_ROOT / ".githooks" / "pre-push"
VALIDATE_DISTRIBUTION = REPO_ROOT / "secondbrain" / "scripts" / "validate_distribution.py"
AUTO_RELEASE = REPO_ROOT / "secondbrain" / "scripts" / "auto_release.py"


def test_auto_release_workflow_exists():
    assert AUTO_RELEASE_WORKFLOW.is_file(), (
        "main-branch version bumps, tags, and release commits must be automated in GitHub Actions"
    )
    assert AUTO_RELEASE.is_file(), (
        "workflow logic must live in a shared Python helper so release semantics stay testable"
    )


def test_auto_release_workflow_triggers_on_pushes_to_main():
    text = AUTO_RELEASE_WORKFLOW.read_text(encoding="utf-8")
    assert "push:" in text
    assert "branches:" in text
    assert "main" in text


def test_auto_release_workflow_skips_bot_release_loops_and_publishes_release():
    text = AUTO_RELEASE_WORKFLOW.read_text(encoding="utf-8")
    assert "github-actions[bot]" in text or "release:" in text, (
        "auto-release must skip the follow-up push it creates itself"
    )
    assert "git tag -a" in text, "auto-release must create an annotated semver tag"
    assert "HEAD:main" in text, "auto-release must push the release commit back to main"
    assert "auto_release.py" in text, (
        "workflow must use the shared auto_release.py helper rather than duplicating semver logic inline"
    )
    assert "bump_version.py" in text, (
        "workflow must rewrite version-managed files through bump_version.py"
    )
    assert "gh release create" in text or "gh release edit" in text, (
        "auto-release must publish the GitHub release itself because bot-created tags do not trigger downstream workflows"
    )
    assert "gh release upload" in text, (
        "auto-release must upload the installable release asset on the same run that creates the tag"
    )
    assert "gh release download" in text, (
        "auto-release must validate the published release asset, not just the locally built zip"
    )


def test_publish_release_workflow_uploads_and_revalidates_published_asset():
    text = PUBLISH_RELEASE_WORKFLOW.read_text(encoding="utf-8")
    assert "git archive --format=zip" in text, (
        "release workflow must build the shipped plugin zip from tracked files "
        "so Cowork users can install the latest release directly"
    )
    assert "gh release upload" in text, (
        "release workflow must upload the release zip asset promised by README.md"
    )
    assert "gh release download" in text, (
        "release workflow must download the just-published asset so validation covers the real release artifact"
    )
    assert text.count("validate_distribution.py") >= 2, (
        "release workflow must validate both the built ZIP and the downloaded published asset"
    )


def test_publish_release_workflow_preserves_release_history():
    text = PUBLISH_RELEASE_WORKFLOW.read_text(encoding="utf-8")
    assert "gh release delete" not in text, (
        "release workflow must not delete older semver releases; deleting "
        "history races with concurrent tag pushes and breaks release URLs"
    )
    assert 'git push origin ":refs/tags/$old"' not in text, (
        "release workflow must not delete semver tags; removing the current tag "
        "turns the release into an untagged draft"
    )


def test_pre_push_is_validation_only():
    text = PRE_PUSH.read_text(encoding="utf-8")
    assert "validate_distribution.py" in text, (
        "pre-push must validate packaging and the local Claude install smoke path"
    )
    assert "--claude-smoke" in text, (
        "pre-push must still run the local Claude marketplace/install smoke test"
    )
    assert "release_workflow.py" not in text, (
        "pre-push must not compute or mutate release state locally anymore"
    )
    assert "--release" not in text and "--tag" not in text, (
        "pre-push must not create commits or tags; release mutation belongs in GitHub Actions"
    )
    assert "run 'git push' again" not in text, (
        "pre-push must not require a second push after mutating the repo"
    )
