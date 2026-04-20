"""String-contract tests for dream-protocol's hot-memory regeneration phase.

Originally written for "Phase 7" in v3.5.x; renumbered to "Phase 6" in v3.6
when the standalone commit phase was removed (vault git versioning dropped).
The phase still regenerates `brain/hot-memory.md` from scratch via
`update_hot_memory.py --regenerate`, keeping the schema validator and
token-budget enforcement in one place.

The same pass also removes a dead `${TRANSCRIPTS_DIR}` reference from
Phase 2's gather-signal section. The variable was never defined anywhere
and grep'ing raw JSONL transcripts is no longer dream-protocol's job now
that the Stop hook + ingester handle real-time ingest.

These tests are deliberately string-level contracts: dream-protocol is a
prompt-driven skill, and the skill body is instructions for the agent,
not executable code.

Scope:
    - The regenerate phase exists (currently labeled "Phase 6")
    - It references `update_hot_memory.py` with `--regenerate`
    - It describes a fail-soft path when the script errors
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


def _regenerate_phase_body() -> str:
    """Extract the hot-memory regenerate phase body from the skill.

    Anchors on "Regenerate hot memory" so renumbering Phase 7 → Phase 6
    (v3.6) didn't break the test. Returns everything from that heading to
    the next top-level "# " heading (or EOF).
    """
    content = _content()
    start = content.find("Regenerate hot memory")
    assert start != -1, (
        "dream-protocol skill must contain a 'Regenerate hot memory' phase"
    )
    # Back up to the start of the heading line for readability in failures.
    line_start = content.rfind("\n", 0, start) + 1
    rest = content[line_start:]
    heading_idx = rest.find("\n# ", 10)
    return rest if heading_idx == -1 else rest[:heading_idx]


class TestRegeneratePhaseExists:
    def test_section_header_present(self):
        content = _content()
        assert "Regenerate hot memory" in content, (
            "dream-protocol must have a hot-memory regeneration phase — "
            "the nightly step that keeps brain/hot-memory.md consistent "
            "with the now-clean vault state."
        )

    def test_mentions_hot_memory(self):
        body = _regenerate_phase_body()
        assert "hot-memory" in body.lower() or "hot memory" in body.lower(), (
            "The regenerate phase body must mention hot-memory — that's "
            "the file being regenerated."
        )


class TestUpdateScriptReference:
    def test_references_update_hot_memory_script(self):
        body = _regenerate_phase_body()
        assert "update_hot_memory.py" in body, (
            "The regenerate phase must reference update_hot_memory.py — "
            "the canonical writer for brain/hot-memory.md. Direct MCP "
            "writes would skip the schema validator."
        )

    def test_uses_regenerate_flag(self):
        body = _regenerate_phase_body()
        assert "--regenerate" in body, (
            "The phase must invoke update_hot_memory.py with --regenerate "
            "— this is the full-rebuild path, not the --apply incremental "
            "path used by the ingester."
        )

    def test_references_vault_path_variable(self):
        body = _regenerate_phase_body()
        assert "VAULT_PATH" in body or "--vault" in body, (
            "The phase must pass the vault path to update_hot_memory.py "
            "so the script knows which vault to regenerate."
        )


class TestFailSoftDocumented:
    def test_documents_failure_behavior(self):
        """If the script errors, the existing hot-memory must stay in
        place — the next ingest cycle continues updating it incrementally.
        """
        body = _regenerate_phase_body()
        low = body.lower()
        fail_soft_phrases = [
            "existing hot-memory",
            "leave the existing",
            "ingest-log",
            "fail",
            "logs",
            "log to",
        ]
        assert any(phrase in low for phrase in fail_soft_phrases), (
            "The regenerate phase must describe fail-soft behavior — the "
            "existing hot-memory stays in place if regeneration errors, "
            "and the next ingest cycle keeps it current."
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
