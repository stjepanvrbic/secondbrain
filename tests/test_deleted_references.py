"""Regression lint for files that Phase 3 (T11-T14) retired.

Phase 3 of the lifecycle redesign retired a set of files and slash commands
that used to be part of the session start/end discipline. Before T11 the
plugin shipped a `session-start` SKILL.md that the agent invoked manually;
before T13 a `session-end` SKILL.md served the same role at the other end of
the session. Both were replaced by subagent-driven hooks (emit-hot-memory.sh
for start, on-stop.sh -> secondbrain-ingester for end).

When files get retired, stale references linger in three common places:

1. Markdown files telling the agent to run a slash command that no longer
   exists (e.g., "run /secondbrain:session-start now").
2. Hook scripts still shelling out to a deleted helper.
3. Dead Python helpers that lost their only consumer but nobody deleted
   the file.

This test file guards the delete. It does NOT replace the skill-consistency
lint (which enforces plugin path resolution in general) — it is a sharper
check that the SPECIFIC retired items never quietly come back.

If you are INTENTIONALLY un-retiring one of these (don't), you must also
remove its row here. The test is designed to be loud when it fires.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = REPO_ROOT / "secondbrain"
SKILLS_DIR = PLUGIN_ROOT / "skills"
REFERENCES_DIR = PLUGIN_ROOT / "references"
HOOKS_DIR = PLUGIN_ROOT / "hooks"
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"


# ----------------------------------------------------------------------
# Part 1: The retired files must not exist at their old locations
# ----------------------------------------------------------------------

class TestRetiredFilesAreGone:
    def test_session_start_skill_deleted(self) -> None:
        """T11 retired secondbrain/skills/session-start/SKILL.md."""
        path = SKILLS_DIR / "session-start" / "SKILL.md"
        assert not path.exists(), (
            f"T11 retired {path.relative_to(REPO_ROOT)}; the session-start "
            f"skill is now owned by the emit-hot-memory hook. If you are "
            f"un-retiring it, also remove this test row."
        )

    def test_session_end_skill_deleted(self) -> None:
        """T13 retired secondbrain/skills/session-end/SKILL.md."""
        path = SKILLS_DIR / "session-end" / "SKILL.md"
        assert not path.exists(), (
            f"T13 retired {path.relative_to(REPO_ROOT)}; session-end flushing "
            f"is now owned by the Stop hook and secondbrain-ingester subagent."
        )

    def test_session_start_hook_deleted(self) -> None:
        """T11 retired secondbrain/hooks/session-start.sh in favor of
        emit-hot-memory.sh."""
        path = HOOKS_DIR / "session-start.sh"
        assert not path.exists(), (
            f"T11 retired {path.relative_to(REPO_ROOT)} in favor of "
            f"emit-hot-memory.sh. hooks.json must wire SessionStart to "
            f"the new hook, not the old one."
        )

    def test_session_start_bootstrap_reference_deleted(self) -> None:
        """T11 moved session-start-bootstrap.md out of references/ to
        docs/session-start-architecture.md."""
        path = REFERENCES_DIR / "session-start-bootstrap.md"
        assert not path.exists(), (
            f"T11 moved {path.relative_to(REPO_ROOT)} out of references/. "
            f"Check docs/session-start-architecture.md for its historical "
            f"content instead."
        )


# ----------------------------------------------------------------------
# Part 2: Active markdown must not tell the agent to run the retired
# slash commands
# ----------------------------------------------------------------------
#
# We only scan active-instruction markdown: SKILL.md files and reference
# docs that the agent actually loads at runtime. Test fixtures and
# historical-note docs (docs/session-start-architecture.md, pre-existing
# tests asserting absence) are allowed to mention the names to document
# what happened.

RETIRED_SLASH_COMMANDS: tuple[str, ...] = (
    "/secondbrain:session-start",
    "/secondbrain:session-end",
)

# Files allowed to mention the retired slash commands — every one of these
# should be either (a) test code verifying the name is NOT referenced in
# active prose, or (b) developer documentation explaining what was retired.
ACTIVE_PROSE_ALLOWLIST: frozenset[str] = frozenset({
    # Developer-facing architecture narrative. Must explain the retirement.
    "secondbrain/docs/session-start-architecture.md",
    # T11/T13 left explanatory comments at the top of the hooks that
    # replaced them. These are one-line historical markers, not live
    # references telling the agent to invoke the old name.
    "secondbrain/hooks/session-end.sh",
})


def _iter_active_prose() -> list[Path]:
    """Every markdown file inside the plugin that the agent loads at
    runtime (SKILL.md files, reference docs, agent definitions,
    scheduled tasks)."""
    files: list[Path] = []
    for pattern in ("skills/**/*.md", "references/**/*.md",
                    "agents/**/*.md", "scheduled-tasks/**/*.md"):
        files.extend(sorted(PLUGIN_ROOT.glob(pattern)))
    # Hook scripts (.sh) are loaded by the harness, not by the agent,
    # but if a hook shells out to a slash command, it's still wrong.
    files.extend(sorted(PLUGIN_ROOT.glob("hooks/*.sh")))
    return files


class TestNoActiveReferencesToRetiredSlashCommands:
    def test_retired_slash_commands_only_in_allowlisted_files(self) -> None:
        offenders: list[str] = []

        for path in _iter_active_prose():
            rel_path = path.relative_to(REPO_ROOT).as_posix()
            if rel_path in ACTIVE_PROSE_ALLOWLIST:
                continue
            try:
                text = path.read_text(errors="replace")
            except OSError as e:
                offenders.append(f"{rel_path}: unreadable ({e})")
                continue
            for bad in RETIRED_SLASH_COMMANDS:
                if bad in text:
                    offenders.append(
                        f"{rel_path}: contains retired slash command "
                        f"{bad!r}"
                    )

        assert not offenders, (
            "Plugin files reference retired slash commands. Either remove "
            "the reference or — if the file legitimately documents the "
            "retirement — add it to ACTIVE_PROSE_ALLOWLIST.\n\n"
            + "\n".join(f"  - {o}" for o in offenders)
        )


# ----------------------------------------------------------------------
# Part 3: vault_guide.py — either still has live consumers or is deleted
# ----------------------------------------------------------------------
#
# T11 speculated that vault_guide.py might be an orphan left over from the
# session-start skill. It is NOT — init/SKILL.md's final-verification step
# still calls it for its vault summary output. This test just asserts
# the consumer contract: if somebody deletes vault_guide.py, they also
# delete the init/SKILL.md call site (and vice versa).

class TestVaultGuideConsumerContract:
    def test_vault_guide_and_init_are_in_lockstep(self) -> None:
        script = SCRIPTS_DIR / "vault_guide.py"
        init_skill = SKILLS_DIR / "init" / "SKILL.md"

        script_exists = script.exists()
        init_text = init_skill.read_text() if init_skill.exists() else ""
        init_mentions = "vault_guide.py" in init_text

        if script_exists:
            assert init_mentions, (
                "vault_guide.py exists but init/SKILL.md no longer calls "
                "it. This is orphaned code — either restore the call site "
                "or delete the script."
            )
        else:
            assert not init_mentions, (
                "init/SKILL.md still calls vault_guide.py but the script "
                "has been deleted. Remove the call site to avoid a runtime "
                "crash during /secondbrain:init."
            )
