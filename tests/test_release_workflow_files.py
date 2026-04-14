"""Tests for the GitHub Actions release finalizer."""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish-release.yml"
PRE_PUSH = REPO_ROOT / ".githooks" / "pre-push"
VALIDATE_DISTRIBUTION = REPO_ROOT / "secondbrain" / "scripts" / "validate_distribution.py"


def test_publish_release_workflow_exists():
    assert WORKFLOW.is_file(), (
        "release finalization must run in GitHub Actions, not a local post-push script"
    )
    assert VALIDATE_DISTRIBUTION.is_file(), (
        "distribution validation must live in a shared script so pre-push and "
        "release CI validate the same shipped artifact rules"
    )


def test_publish_release_workflow_triggers_on_semver_tags():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "push:" in text
    assert "tags:" in text
    assert "v*" in text


def test_publish_release_workflow_uploads_cowork_zip_asset():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "git archive --format=zip" in text, (
        "release workflow must build the shipped plugin zip from tracked files "
        "so Cowork users can install the latest release directly"
    )
    assert "--prefix=secondbrain/" in text, (
        "release zip must contain the shipped plugin under a top-level "
        "`secondbrain/` directory"
    )
    assert "gh release upload" in text, (
        "release workflow must upload the release zip asset promised by README.md"
    )
    assert "secondbrain-${tag}.zip" in text, (
        "release asset name must stay aligned with the README's "
        "`secondbrain-vX.Y.Z.zip` install instructions"
    )
    assert "validate_distribution.py" in text, (
        "release workflow must validate the built ZIP before upload so Cowork "
        "never gets a malformed release asset"
    )


def test_publish_release_workflow_preserves_release_history():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "gh release delete" not in text, (
        "release workflow must not delete older semver releases; deleting "
        "history races with concurrent tag pushes and breaks release URLs"
    )
    assert 'git push origin ":refs/tags/$old"' not in text, (
        "release workflow must not delete semver tags; removing the current tag "
        "turns the release into an untagged draft"
    )


def test_publish_release_workflow_marks_only_highest_semver_latest():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "--sort=-version:refname" in text, (
        "release workflow must compare the pushed tag against the highest semver "
        "tag before deciding which release is latest"
    )
    assert "--latest=false" in text, (
        "older tag workflows must explicitly avoid marking their release latest "
        "when several missing tags are pushed together"
    )


def test_pre_push_uses_pytest_executable_not_python_module_import():
    text = PRE_PUSH.read_text(encoding="utf-8")
    assert "command -v pytest" in text
    assert "python3 -m pytest" not in text
    assert 'python3 -c "import pytest"' not in text


def test_pre_push_runs_distribution_smoke_validation():
    text = PRE_PUSH.read_text(encoding="utf-8")
    assert "validate_distribution.py" in text, (
        "pre-push must validate the release ZIP and the local Claude "
        "marketplace/install smoke path before allowing a push"
    )
    assert "--claude-smoke" in text, (
        "pre-push must run the local Claude marketplace/install smoke test "
        "when validating a release candidate"
    )
