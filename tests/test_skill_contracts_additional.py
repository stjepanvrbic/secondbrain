"""String-contract coverage for under-tested operational skills."""

from __future__ import annotations

from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parent.parent / "secondbrain"


def _skill_text(rel_path: str) -> str:
    return (PLUGIN_ROOT / rel_path).read_text(encoding="utf-8")


def test_deadline_check_mentions_status_deadlines_and_promotions():
    text = _skill_text("skills/deadline-check/SKILL.md")
    assert "brain/status.md" in text
    assert "brain/deadlines.md" in text
    assert "OVERDUE" in text and "CRITICAL" in text and "URGENT" in text
    assert "Auto-Promote" in text


def test_email_triage_enforces_zero_unread_and_validation_before_mutation():
    text = _skill_text("skills/email-triage/SKILL.md")
    assert "zero unread" in text.lower()
    assert "verify_vault.py --modified-only" in text
    assert "No Gmail state mutation" in text
    assert "In-Flight Manifest" in text


def test_email_triage_stays_under_dispatch_prompt_budget():
    text = _skill_text("skills/email-triage/SKILL.md")
    assert len(text) <= 11000, (
        "email-triage is part of Cowork scheduled dispatch. Keep the default "
        "skill body compact so bridge sessions do not bloat as quickly."
    )


def test_end_of_day_requires_brain_dump_prompt_and_session_log():
    text = _skill_text("skills/end-of-day/SKILL.md")
    assert "Prompt for Brain Dump" in text
    assert "brain/session-log.md" in text
    assert "Leaving information only in conversation" in text


def test_knowledge_search_forbids_answering_from_memory_and_requires_sources():
    text = _skill_text("skills/knowledge-search/SKILL.md")
    assert "Vault is the source of truth" in text
    assert "Never guess" in text or "Never guess the answer" in text
    assert "Citation Format" in text
    assert "Answering from general knowledge" in text


def test_vault_review_requires_validation_and_review_modes():
    text = _skill_text("skills/vault-review/SKILL.md")
    assert "Focused: Deadline Review" in text
    assert "Full: Weekly Audit" in text
    assert "verify_vault.py" in text
    assert "Auto-Promotion Rules" in text


def test_weekly_review_requires_manifest_rebuild_after_validation():
    text = _skill_text("skills/weekly-review/SKILL.md")
    assert "Build Next Week's Plan" in text
    assert "rebuild_manifest.py" in text
    assert "Post-Write Validation" in text


def test_whats_next_enforces_single_task_dispatch_and_energy_matching():
    text = _skill_text("skills/whats-next/SKILL.md")
    assert "Pick ONE task" in text
    assert "Energy Mapping" in text
    assert "Presenting a list of options" in text
    assert "Morning Mode Algorithm" in text
