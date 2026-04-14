"""String-contract tests for secondbrain/skills/cowork-debug/SKILL.md.

The cowork-debug skill is a shipped reference skill for Cowork log and
transcript inspection. These tests pin the critical path anchors so the
skill stays useful:

1. It exists in the shipped plugin tree and has valid frontmatter.
2. It names the canonical macOS Cowork app-state root.
3. It distinguishes Dispatch bridge artifacts from worker and regular
   conversation transcripts.
4. It includes the key path anchors needed to find secondbrain runs.
5. It stays descriptive rather than turning into a rigid runbook.
"""

from __future__ import annotations

import re
from pathlib import Path


SKILL_PATH = (
    Path(__file__).resolve().parent.parent
    / "secondbrain"
    / "skills"
    / "cowork-debug"
    / "SKILL.md"
)


def _content() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


class TestSkillFileExists:
    def test_skill_file_present(self):
        assert SKILL_PATH.is_file(), (
            f"cowork-debug skill must exist at {SKILL_PATH}. "
            "The plugin should ship this reference skill."
        )

    def test_has_yaml_frontmatter(self):
        content = _content()
        assert content.startswith("---"), (
            "cowork-debug/SKILL.md must start with YAML frontmatter."
        )
        second = content.find("---", 3)
        assert second > 0, (
            "cowork-debug/SKILL.md frontmatter must be closed by a "
            "second '---' delimiter."
        )

    def test_has_required_frontmatter_fields(self):
        content = _content()
        assert "name: cowork-debug" in content, (
            "cowork-debug/SKILL.md must declare `name: cowork-debug`."
        )
        frontmatter = content[: content.find("---", 3)]
        assert "description:" in frontmatter, (
            "cowork-debug/SKILL.md must include a `description:` field."
        )
        assert re.search(r'version:\s*"[^"]+"', content), (
            "cowork-debug/SKILL.md must include a version in frontmatter "
            "so bump_version.py can keep it in sync."
        )


class TestCoreContent:
    def test_has_core_rule_section(self):
        assert "Core Rule" in _content(), (
            "cowork-debug should start with a 'Core Rule' section."
        )

    def test_names_canonical_cowork_root(self):
        assert "~/Library/Application Support/Claude" in _content(), (
            "cowork-debug must name the canonical macOS Cowork app-state root."
        )

    def test_names_key_dispatch_and_transcript_paths(self):
        text = _content()
        required = [
            "bridge-state.json",
            "local-agent-mode-sessions",
            "local_ditto",
            "audit.jsonl",
            ".claude/projects",
            "local_<uuid>.json",
        ]
        missing = [item for item in required if item not in text]
        assert not missing, (
            "cowork-debug is missing required path anchors:\n"
            + "\n".join(f"  - {item}" for item in missing)
        )

    def test_distinguishes_dispatch_bridge_from_regular_transcripts(self):
        text = _content().lower()
        assert "dispatch bridge" in text, (
            "cowork-debug must explicitly describe the dispatch bridge layer."
        )
        assert "regular conversation" in text, (
            "cowork-debug must explicitly describe regular conversations."
        )
        assert "worker transcript" in text or "dispatched worker" in text, (
            "cowork-debug must distinguish dispatched worker transcripts "
            "from the bridge audit."
        )

    def test_includes_secondbrain_scheduled_task_identifiers(self):
        text = _content()
        assert "<scheduled-task" in text, (
            "cowork-debug must explain how scheduled tasks appear in metadata."
        )
        assert "initialMessage" in text, (
            "cowork-debug must mention metadata fields used to identify "
            "scheduled secondbrain runs."
        )
        assert "secondbrain" in text.lower(), (
            "cowork-debug must explicitly mention secondbrain-specific runs."
        )

    def test_mentions_prompt_too_long_bridge_overflow_case(self):
        text = _content()
        assert "Prompt is too long" in text, (
            "cowork-debug should cover the bridge overflow failure signature."
        )


class TestSkillStaysDescriptive:
    def test_declares_reference_not_fixed_procedure(self):
        text = _content().lower()
        assert "not a fixed procedure" in text or "not a mandatory checklist" in text, (
            "cowork-debug must say it is a reference, not a rigid workflow."
        )

    def test_tells_agent_to_follow_user_request(self):
        text = _content().lower()
        assert "follow the user's request" in text or "let the user's request drive" in text, (
            "cowork-debug should make it explicit that the invocation "
            "request determines the action."
        )
