"""Regression: _MANIFEST.md.template must reference the new brain
files introduced in Phase 3 — `brain/hot-memory.md` and
`brain/morning-brief.md` (T14).

_MANIFEST.md is the auto-generated vault index rebuilt by
`rebuild_manifest.py` from the template. If the template doesn't list
the new brain files, they won't appear in the regenerated manifest and
the agent's "File Pointers" won't find them — breaking the advertised
discovery contract.

These tests pin the template's Brain files listing so future rebuilds
always surface both files.
"""

from __future__ import annotations

from pathlib import Path

TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent
    / "secondbrain"
    / "references"
    / "_MANIFEST.md.template"
)


def _template() -> str:
    return TEMPLATE_PATH.read_text()


class TestTemplateExists:
    def test_file_exists(self):
        assert TEMPLATE_PATH.is_file(), (
            f"_MANIFEST.md.template must exist at {TEMPLATE_PATH}"
        )


class TestBrainHotMemoryRow:
    def test_template_mentions_hot_memory(self):
        assert "brain/hot-memory" in _template(), (
            "_MANIFEST.md.template must reference brain/hot-memory.md "
            "so the regenerated manifest surfaces it. Hot-memory is "
            "the always-loaded SessionStart context file."
        )

    def test_hot_memory_is_in_brain_section(self):
        """The listing should live in the Brain (brain/) section of the
        template, not in a one-off place. We check by locating the
        '## brain/' heading and confirming hot-memory appears below it
        before the next top-level '##' heading.
        """
        content = _template()
        section_start = content.find("## brain/")
        assert section_start != -1, "template must have a '## brain/' section"
        # The brain section ends at the next '---' separator or '## '
        next_section = content.find("\n## ", section_start + 10)
        section_end = next_section if next_section != -1 else len(content)
        section = content[section_start:section_end]
        assert "brain/hot-memory" in section, (
            "brain/hot-memory.md must be listed inside the brain/ "
            "section of the manifest template."
        )


class TestBrainMorningBriefRow:
    def test_template_mentions_morning_brief(self):
        assert "brain/morning-brief" in _template(), (
            "_MANIFEST.md.template must reference brain/morning-brief.md "
            "so the regenerated manifest surfaces the cached brief."
        )

    def test_morning_brief_is_in_brain_section(self):
        content = _template()
        section_start = content.find("## brain/")
        assert section_start != -1
        next_section = content.find("\n## ", section_start + 10)
        section_end = next_section if next_section != -1 else len(content)
        section = content[section_start:section_end]
        assert "brain/morning-brief" in section, (
            "brain/morning-brief.md must be listed inside the brain/ "
            "section of the manifest template."
        )
