"""Tests for the GitHub Actions release finalizer."""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish-release.yml"
PRE_PUSH = REPO_ROOT / ".githooks" / "pre-push"


def test_publish_release_workflow_exists():
    assert WORKFLOW.is_file(), (
        "release finalization must run in GitHub Actions, not a local post-push script"
    )


def test_publish_release_workflow_triggers_on_semver_tags():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "push:" in text
    assert "tags:" in text
    assert "v*" in text


def test_pre_push_uses_pytest_executable_not_python_module_import():
    text = PRE_PUSH.read_text(encoding="utf-8")
    assert "command -v pytest" in text
    assert "python3 -m pytest" not in text
    assert 'python3 -c "import pytest"' not in text
