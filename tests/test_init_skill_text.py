"""String-contract tests for secondbrain/skills/init/SKILL.md — T3.

These are regression guards, not behavior tests. They pin down textual
invariants the skill must uphold:

1. The marker path must always refer to `${VAULT_PATH}/.secondbrain-installed`,
   never `~/.secondbrain-installed` — the latter is a bug (init_obsidian.py
   writes the marker inside the vault, so checking home dir never finds it).
2. The dead "If `git init` fails: skip without error" line must be gone —
   the current init flow has no git init call, and Phase 2 will introduce
   real git handling via setup_steps.setup_git().
"""

from __future__ import annotations

from pathlib import Path


SKILL_PATH = (
    Path(__file__).resolve().parent.parent
    / "secondbrain"
    / "skills"
    / "init"
    / "SKILL.md"
)


class TestMarkerPath:
    def test_no_home_dir_marker_reference(self):
        content = SKILL_PATH.read_text()
        assert "~/.secondbrain-installed" not in content, (
            "init skill references the marker at ~/.secondbrain-installed, but "
            "init_obsidian.py writes it to ${VAULT_PATH}/.secondbrain-installed. "
            "The home-dir check is a bug — the skill will never find an "
            "existing install."
        )

    def test_vault_path_marker_is_referenced(self):
        # Sanity: the correct path is still documented somewhere in the skill.
        content = SKILL_PATH.read_text()
        assert "${VAULT_PATH}/.secondbrain-installed" in content, (
            "Expected at least one reference to the vault-internal marker "
            "path in the init skill."
        )


class TestNoDeadGitInit:
    def test_no_git_init_error_handling_line(self):
        content = SKILL_PATH.read_text()
        assert "If `git init` fails: skip without error" not in content, (
            "The init skill still has orphaned error handling for a git init "
            "step that doesn't exist. Phase 2 will introduce real git support "
            "via setup_steps.setup_git(); until then this line is dead code."
        )
