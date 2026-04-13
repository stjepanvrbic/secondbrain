"""String-contract tests for updated runtime and entity documentation."""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = REPO_ROOT / "secondbrain"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_readme_drops_stale_16_point_doctor_wording():
    text = _read(REPO_ROOT / "README.md")
    assert "16-point" not in text
    assert "16-check" not in text
    assert "/secondbrain:doctor" in text


def test_readme_uses_preferences_local_agent_mode_trusted_folders():
    text = _read(REPO_ROOT / "README.md")
    assert "preferences.localAgentModeTrustedFolders" in text


def test_doctor_skill_uses_merged_report_flow():
    text = _read(PLUGIN_ROOT / "skills" / "doctor" / "SKILL.md")
    assert "doctor_cli.py --diagnose" in text
    assert "doctor_report.py" in text
    assert ".scheduled-tasks" not in text
    assert "Do NOT parse or summarize the raw CLI output first" in text


def test_init_skill_uses_merged_doctor_verification():
    text = _read(PLUGIN_ROOT / "skills" / "init" / "SKILL.md")
    assert "doctor_cli.py --diagnose" in text
    assert "doctor_report.py" in text
    assert "Parse the summary line" not in text
    assert ".scheduled-tasks" not in text


def test_ingestion_rules_document_smart_entity_resolution():
    text = _read(PLUGIN_ROOT / "references" / "ingestion-rules.md")
    assert "aliases:" in text
    assert "Parent fallback" in text
    assert "[verify:: true]" in text


def test_entity_template_includes_aliases_and_parent_entity():
    text = _read(PLUGIN_ROOT / "references" / "templates.md")
    assert "aliases:" in text
    assert "parent_entity:" in text


def test_environment_reference_documents_cowork_schedule_and_overrides():
    text = _read(PLUGIN_ROOT / "references" / "environments.md")
    assert "no supported `.scheduled-tasks/` directory" in text
    assert "SECONDBRAIN_CLAUDE_DESKTOP_CONFIG" in text
