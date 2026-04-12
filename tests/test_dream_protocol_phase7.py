"""String-contract tests for dream-protocol's new Phase 7 (T14).

Phase 7 — "Regenerate hot memory" — runs after Phase 6's commit and
regenerates `brain/hot-memory.md` from scratch based on the now-clean
vault state. It delegates to `update_hot_memory.py --regenerate` so the
writer, schema validation, and token-budget enforcement all live in one
place.

The same T14 pass also removes a dead `${TRANSCRIPTS_DIR}` reference
from Phase 2's gather-signal section. The variable was never defined
anywhere and grep'ing raw JSONL transcripts is no longer dream-protocol's
job now that the Stop hook + ingester handle real-time ingest.

These tests are deliberately string-level contracts: dream-protocol is a
prompt-driven skill, and the skill body is instructions for the agent,
not executable code.

Scope:
    - "Phase 7" section header exists
    - Phase 7 appears AFTER Phase 6
    - Phase 7 references `update_hot_memory.py` with `--regenerate`
    - Phase 7 describes a fail-soft path when the script errors
    - The dead `${TRANSCRIPTS_DIR}` reference is gone
"""

from __future__ import annotations

from pathlib import Path

SKILL_PATH = (
    Path(__file__).resolve().parent.parent
    / "secondbrain"
    / "skills"
    / "dream-protocol"
    / "SKILL.md"
)


def _content() -> str:
    return SKILL_PATH.read_text()


def _phase_7_body() -> str:
    """Extract the Phase 7 section from the dream-protocol skill body.

    Phase 7 starts at the "# Phase 7" heading and ends at the next top-
    level "# " heading or EOF.
    """
    content = _content()
    start = content.find("Phase 7")
    assert start != -1, "Phase 7 section missing from dream-protocol skill"
    rest = content[start:]
    heading_idx = rest.find("\n# ", 10)
    return rest if heading_idx == -1 else rest[:heading_idx]


class TestPhase7Exists:
    def test_phase_7_section_header(self):
        content = _content()
        assert "Phase 7" in content, (
            "dream-protocol must have a Phase 7 section — the nightly "
            "hot-memory regeneration step that keeps brain/hot-memory.md "
            "consistent with the now-clean vault state."
        )

    def test_phase_7_is_after_phase_6(self):
        content = _content()
        p6 = content.find("Phase 6")
        p7 = content.find("Phase 7")
        assert p6 != -1, "Phase 6 must exist"
        assert p7 != -1, "Phase 7 must exist"
        assert p7 > p6, (
            "Phase 7 must appear AFTER Phase 6 — regeneration happens "
            "after the commit so the hot-memory update reflects the "
            "committed state."
        )

    def test_phase_7_mentions_hot_memory(self):
        phase_7 = _phase_7_body()
        assert "hot-memory" in phase_7.lower() or "hot memory" in phase_7.lower(), (
            "Phase 7 body must mention hot-memory — that's the file being "
            "regenerated."
        )


class TestPhase7UpdateScriptReference:
    def test_phase_7_references_update_hot_memory_script(self):
        phase_7 = _phase_7_body()
        assert "update_hot_memory.py" in phase_7, (
            "Phase 7 must reference update_hot_memory.py — the canonical "
            "writer for brain/hot-memory.md. Direct MCP writes would skip "
            "the schema validator."
        )

    def test_phase_7_uses_regenerate_flag(self):
        phase_7 = _phase_7_body()
        assert "--regenerate" in phase_7, (
            "Phase 7 must invoke update_hot_memory.py with the "
            "--regenerate flag — this is the full-rebuild path, not the "
            "--apply incremental path used by the ingester."
        )

    def test_phase_7_references_vault_path_variable(self):
        """Phase 7's script invocation should pass the vault path — the
        skill uses ${VAULT_PATH} or $VAULT_PATH throughout for consistency
        with other phases that also shell out.
        """
        phase_7 = _phase_7_body()
        assert "VAULT_PATH" in phase_7 or "--vault" in phase_7, (
            "Phase 7 must pass the vault path to update_hot_memory.py so "
            "the script knows which vault to regenerate."
        )


class TestPhase7FailSoft:
    def test_phase_7_documents_failure_behavior(self):
        """If the script errors, the existing hot-memory must stay in
        place — the next ingest cycle continues updating it incrementally.
        """
        phase_7 = _phase_7_body()
        low = phase_7.lower()
        fail_soft_phrases = [
            "existing hot-memory",
            "leave the existing",
            "ingest-log",
            "fail",
            "logs",
            "log to",
        ]
        assert any(phrase in low for phrase in fail_soft_phrases), (
            "Phase 7 must describe fail-soft behavior — the existing "
            "hot-memory stays in place if regeneration errors, and the "
            "next ingest cycle keeps it current."
        )


class TestDeadTranscriptsDirReferenceRemoved:
    def test_transcripts_dir_variable_is_gone(self):
        """The dead `${TRANSCRIPTS_DIR}` reference in Phase 2 must be
        removed. The variable was never defined anywhere in the plugin,
        and transcript signal now flows through the Stop-hook ingester.
        """
        content = _content()
        assert "TRANSCRIPTS_DIR" not in content, (
            "TRANSCRIPTS_DIR was an undefined shell variable referenced "
            "in Phase 2's gather-signal step. Remove the whole transcript-"
            "grep bullet; real-time ingest handles transcript signal now."
        )
