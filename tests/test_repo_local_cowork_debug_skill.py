"""Regression tests for the repo-local cowork-debug skill.

This skill is for Codex development inside this repository. It must live
under `.codex/skills/`, and it must NOT be shipped as part of the
secondbrain plugin runtime.
"""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
REPO_SKILL = REPO_ROOT / ".codex" / "skills" / "cowork-debug" / "SKILL.md"
REPO_SKILL_UI = REPO_ROOT / ".codex" / "skills" / "cowork-debug" / "agents" / "openai.yaml"
PLUGIN_SKILL = REPO_ROOT / "secondbrain" / "skills" / "cowork-debug" / "SKILL.md"


def _text() -> str:
    return REPO_SKILL.read_text(encoding="utf-8")


def test_repo_local_skill_exists():
    assert REPO_SKILL.is_file(), (
        "cowork-debug must live under .codex/skills for repo-local Codex use."
    )


def test_repo_local_skill_has_basic_frontmatter():
    text = _text()
    assert text.startswith("---")
    assert "name: cowork-debug" in text
    assert "description:" in text


def test_repo_local_skill_has_ui_metadata():
    assert REPO_SKILL_UI.is_file(), (
        "Repo-local Codex skills should ship agents/openai.yaml so they show "
        "up cleanly in the skill surface."
    )


def test_repo_local_skill_mentions_dispatch_and_regular_transcripts():
    text = _text()
    assert "bridge-state.json" in text
    assert "local_ditto" in text
    assert ".claude/projects" in text
    assert "regular conversation" in text.lower()
    assert "secondbrain" in text.lower()


def test_cowork_debug_is_not_packaged_as_plugin_skill():
    assert not PLUGIN_SKILL.exists(), (
        "cowork-debug is repo-local development context and must not ship "
        "inside secondbrain/skills."
    )
