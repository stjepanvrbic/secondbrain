"""String-contract tests for the T13 refactored ingest skill.

Before T13, `secondbrain/skills/ingest/SKILL.md` was a monolith that did
routing, extraction, and writes directly in the main agent's context.
After T13 it becomes a thin dispatcher that delegates all work to the
secondbrain-ingester subagent via the Task tool.

Why: ingestion pulls in a lot of content and reference files; running it
in the main agent's context spends tokens the user doesn't want to spend.
Moving to a subagent keeps the main session thin and lets the background
ingester (Stop hook path) and the explicit brain-dump path share one
subagent implementation.

This file guards the contract: the skill body MUST tell the main agent
to dispatch, not to process inline. Drift here lets the main agent
quietly return to running ingest in its own context.
"""

from __future__ import annotations

from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent / "secondbrain"
INGEST_SKILL = PLUGIN_ROOT / "skills" / "ingest" / "SKILL.md"


@pytest.fixture(scope="module")
def skill_text() -> str:
    assert INGEST_SKILL.is_file(), f"ingest skill missing at {INGEST_SKILL}"
    return INGEST_SKILL.read_text(encoding="utf-8")


class TestDispatcherContract:
    def test_mentions_task_tool_invocation(self, skill_text: str):
        # Either literal "Task tool" or the subagent-dispatch keyword.
        low = skill_text.lower()
        assert "task tool" in low or "dispatch" in low, (
            "ingest skill must tell the agent to dispatch via the Task "
            "tool (or equivalent language). Direct processing is the "
            "pre-T13 behavior and is forbidden."
        )

    def test_mentions_secondbrain_ingester_subagent(self, skill_text: str):
        assert "secondbrain-ingester" in skill_text, (
            "ingest skill must reference the 'secondbrain-ingester' "
            "subagent by name — this is the subagent_type argument to "
            "the Task tool."
        )

    def test_mentions_subagent_type_in_task_invocation(self, skill_text: str):
        # The Task tool's argument name is `subagent_type`. The skill
        # body should mention it so the dispatch contract is explicit.
        assert "subagent_type" in skill_text

    def test_mentions_foreground_dispatch(self, skill_text: str):
        # Per Q21 the explicit brain-dump path is foreground (the user
        # is waiting for the summary). The background Stop hook path is
        # async. The skill must make the foreground discipline explicit
        # so nobody converts it to background and breaks the UX.
        low = skill_text.lower()
        assert "foreground" in low or "wait" in low, (
            "ingest skill must say 'foreground' or 'wait' — the explicit "
            "dispatch path is synchronous because the user is waiting "
            "for the one-line summary."
        )

    def test_mentions_envelope_construction(self, skill_text: str):
        # The skill body describes building a context envelope for the
        # subagent. Look for 'envelope' which is the documented term.
        assert "envelope" in skill_text.lower()

    def test_mentions_one_line_summary(self, skill_text: str):
        low = skill_text.lower()
        assert "one-line" in low or "one line" in low, (
            "ingest skill must describe returning the subagent's one-line "
            "summary verbatim — main agent must not add prose around it."
        )


class TestForbiddenInlineProcessing:
    def test_forbidden_section_exists(self, skill_text: str):
        assert "Forbidden" in skill_text or "forbidden" in skill_text.lower()

    def test_forbidden_mentions_not_processing_in_own_context(
        self, skill_text: str
    ):
        low = skill_text.lower()
        # Accept any phrasing that says "don't process the brain dump in
        # your own context" — we care about the intent, not the words.
        signals = (
            "in your own context",
            "your own context",
            "main agent context",
            "delegate",
            "subagent does that",
            "the subagent does",
        )
        assert any(s in low for s in signals), (
            "ingest skill Forbidden section must tell the main agent not "
            "to process the brain dump in its own context — delegation to "
            "the subagent is the whole point of T13."
        )
