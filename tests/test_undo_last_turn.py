"""String-contract tests for the undo-last-turn skill (T9).

The /secondbrain:undo-last-turn skill reverts the most recent vault git
commit — which, since T9 wired up the Stop hook, corresponds to the last
agent turn. These tests pin down the critical safety language so the skill
can't silently lose the confirmation flow.

Scope:
    - SKILL.md file exists in the expected location
    - Has frontmatter with a version field
    - References `vault_git.py last-commit-files` (the preview step)
    - References `vault_git.py reset-last-commit` (the destructive step)
    - Has user-facing confirmation language (MUST be present; safety rail)
    - Has a Forbidden Actions section
    - Mentions the vault's git repo NOT the secondbrain plugin's git repo
"""

from __future__ import annotations

from pathlib import Path

SKILL_PATH = (
    Path(__file__).resolve().parent.parent
    / "secondbrain"
    / "skills"
    / "undo-last-turn"
    / "SKILL.md"
)


def _content() -> str:
    return SKILL_PATH.read_text()


# ---------------------------------------------------------------------------
# Existence / frontmatter
# ---------------------------------------------------------------------------

class TestSkillFileExists:
    def test_skill_file_present(self):
        assert SKILL_PATH.is_file(), (
            f"undo-last-turn skill must exist at {SKILL_PATH}. "
            "T9 is supposed to create this file."
        )

    def test_has_yaml_frontmatter(self):
        content = _content()
        assert content.startswith("---"), (
            "undo-last-turn/SKILL.md must start with YAML frontmatter "
            "so Claude Code can parse its metadata."
        )
        # Find the closing '---'. It must come before any real body text.
        second = content.find("---", 3)
        assert second > 0, (
            "undo-last-turn/SKILL.md frontmatter must be closed by a "
            "second '---' delimiter before the body."
        )

    def test_has_version_in_frontmatter(self):
        content = _content()
        # bump_version.py regex matches `  version: "X.Y.Z"` inside the
        # frontmatter block.
        import re
        m = re.search(r'version:\s*"[^"]+"', content)
        assert m is not None, (
            "undo-last-turn/SKILL.md must have a `version: \"X.Y.Z\"` "
            "line in its frontmatter so bump_version.py can keep it in "
            "lockstep with plugin.json."
        )

    def test_has_name_in_frontmatter(self):
        content = _content()
        assert "name: undo-last-turn" in content, (
            "undo-last-turn/SKILL.md frontmatter must declare "
            "`name: undo-last-turn` so Claude Code can register it."
        )

    def test_has_description_in_frontmatter(self):
        content = _content()
        # Extract the frontmatter block and check it has a description.
        end = content.find("---", 3)
        frontmatter = content[:end]
        assert "description:" in frontmatter, (
            "undo-last-turn/SKILL.md frontmatter must include a "
            "`description:` field so Claude Code knows when to surface "
            "the skill."
        )


# ---------------------------------------------------------------------------
# Core script references
# ---------------------------------------------------------------------------

class TestVaultGitReferences:
    def test_references_last_commit_files(self):
        content = _content()
        assert "last-commit-files" in content, (
            "undo-last-turn must call `vault_git.py last-commit-files` "
            "to preview what would be discarded before acting."
        )

    def test_references_reset_last_commit(self):
        content = _content()
        assert "reset-last-commit" in content, (
            "undo-last-turn must call `vault_git.py reset-last-commit` "
            "to perform the actual rollback."
        )

    def test_references_vault_git_py(self):
        content = _content()
        assert "vault_git.py" in content, (
            "undo-last-turn must explicitly name `vault_git.py` so the "
            "skill body is greppable and uses the canonical CLI."
        )


# ---------------------------------------------------------------------------
# Confirmation language — SAFETY RAIL
# ---------------------------------------------------------------------------

class TestConfirmationLanguage:
    """These tests are load-bearing. undo-last-turn is destructive and
    must always confirm with the user before acting. If a future refactor
    drops the confirmation, these tests scream.
    """

    def test_asks_for_confirmation(self):
        content = _content()
        # Any of these phrasings is acceptable — the skill should clearly
        # ask the user to confirm before running.
        confirm_phrases = [
            "Confirm?",
            "confirm",
            "(yes/no)",
            "y/n",
        ]
        assert any(p in content for p in confirm_phrases), (
            "undo-last-turn MUST ask the user for explicit confirmation "
            "before running. No phrase like 'Confirm?', '(yes/no)', or "
            "'y/n' was found in the skill body."
        )

    def test_explicit_yes_no_prompt(self):
        """Stronger check: the skill should include the canonical
        '(yes/no)' prompt form so the user knows exactly what to type.
        """
        content = _content()
        assert "yes" in content.lower() and "no" in content.lower(), (
            "undo-last-turn should present the user with a yes/no "
            "decision. Both 'yes' and 'no' must appear in the body."
        )

    def test_mentions_destructive_nature(self):
        content = _content()
        # At least one of these warning phrases should appear — the user
        # needs to understand they're about to lose changes.
        warn_phrases = [
            "discard",
            "revert",
            "rollback",
            "destructive",
            "lose",
        ]
        assert any(p in content.lower() for p in warn_phrases), (
            "undo-last-turn must warn the user about the destructive "
            "nature of the operation. No warning phrase found."
        )


# ---------------------------------------------------------------------------
# Structure sections
# ---------------------------------------------------------------------------

class TestForbiddenActions:
    def test_has_forbidden_section(self):
        content = _content()
        assert "Forbidden" in content, (
            "undo-last-turn must have a 'Forbidden' section listing "
            "actions the skill must never take (run without confirmation, "
            "touch the plugin repo, etc.)."
        )

    def test_forbids_running_without_confirmation(self):
        content = _content()
        # The Forbidden section must explicitly call out "no confirmation"
        # as forbidden.
        forbidden_idx = content.find("Forbidden")
        assert forbidden_idx != -1
        rest = content[forbidden_idx:]
        assert "confirm" in rest.lower(), (
            "undo-last-turn's Forbidden section must explicitly ban "
            "running without user confirmation."
        )

    def test_forbids_touching_plugin_repo(self):
        """The skill must only touch the vault's git, never the secondbrain
        plugin's git. Guard this explicitly."""
        content = _content()
        forbidden_idx = content.find("Forbidden")
        assert forbidden_idx != -1
        rest = content[forbidden_idx:]
        # Check for any phrasing that covers this — the word "secondbrain"
        # or "plugin" and "repo" together, or similar.
        assert (
            "secondbrain repo" in rest.lower()
            or "plugin repo" in rest.lower()
            or "plugin's git" in rest.lower()
            or "plugin git" in rest.lower()
        ), (
            "undo-last-turn's Forbidden section must make it clear the "
            "skill only touches the vault's git, never the secondbrain "
            "plugin's own git repo."
        )


class TestCoreRule:
    def test_has_core_rule_section(self):
        content = _content()
        # Per convention (see init/SKILL.md, session-end/SKILL.md), every
        # skill has a "Core Rule" section at the top.
        assert "Core Rule" in content, (
            "undo-last-turn should start with a 'Core Rule' section "
            "summarizing what the skill does, per plugin convention."
        )


class TestVaultScoping:
    def test_operates_on_vault_not_plugin(self):
        content = _content()
        # The skill body should reference $VAULT_PATH or equivalent, not
        # imply the secondbrain plugin repo. Assert $VAULT_PATH is present.
        assert "VAULT_PATH" in content, (
            "undo-last-turn must reference $VAULT_PATH explicitly so "
            "it's clear which git repo is being modified."
        )
