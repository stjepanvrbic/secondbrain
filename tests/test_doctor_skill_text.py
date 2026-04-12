"""String-contract tests for secondbrain/skills/doctor/SKILL.md — T5.

These are regression guards, not behavior tests. They pin down textual
invariants the markdown skill must uphold so the two-turn discipline
can't silently regress:

1. Phase 1 section must explicitly forbid mutating state (uses language
   like "MUST NOT make any changes").
2. Skill references `doctor_cli.py` as the invocation mechanism.
3. A "Forbidden Actions" section exists and mentions Turn 1 read-only.
4. Auto-fixable and escalation-only checks are listed explicitly.
5. No stale "just run /secondbrain:init to fix" language in the body —
   that was the old doctor pattern and it conflicts with the new
   two-turn flow.
"""

from __future__ import annotations

from pathlib import Path

SKILL_PATH = (
    Path(__file__).resolve().parent.parent
    / "secondbrain"
    / "skills"
    / "doctor"
    / "SKILL.md"
)


def _content() -> str:
    return SKILL_PATH.read_text()


class TestPhase1ReadOnlyDiscipline:
    def test_phase1_says_must_not_make_any_changes(self):
        content = _content()
        assert "MUST NOT make any changes" in content, (
            "Phase 1 section must contain the exact phrase 'MUST NOT make any "
            "changes' so the read-only contract is impossible to miss."
        )

    def test_phase1_says_stop_after_report(self):
        content = _content()
        # Either explicit STOP or "end" + "want me to" — something that makes
        # the skill visibly pause for user input.
        has_stop = "STOP" in content or "stop" in content.lower()
        has_confirmation_prompt = "want me to" in content.lower()
        assert has_stop and has_confirmation_prompt, (
            "Phase 1 must end by prompting the user and stopping. The skill "
            "body must contain both 'STOP' (or equivalent) and 'want me to'."
        )


class TestDoctorCliReferenced:
    def test_skill_references_doctor_cli(self):
        content = _content()
        assert "doctor_cli.py" in content, (
            "The skill must invoke doctor_cli.py — if it's not referenced, "
            "the agent won't know how to actually run the check engine."
        )

    def test_skill_references_diagnose_mode(self):
        content = _content()
        assert "--diagnose" in content, (
            "Phase 1 must tell the agent to pass --diagnose to doctor_cli."
        )

    def test_skill_references_treat_mode(self):
        content = _content()
        assert "--treat" in content, (
            "Phase 2 must tell the agent to pass --treat to doctor_cli."
        )


class TestForbiddenActionsSection:
    def test_forbidden_actions_section_exists(self):
        content = _content()
        assert "Forbidden Actions" in content, (
            "Skill must have a Forbidden Actions section — it's the hard "
            "guardrail that keeps Turn 1 from mutating state."
        )

    def test_forbidden_actions_mentions_turn_1_readonly(self):
        content = _content()
        # The section must say, in some form, "Turn 1 is read-only."
        assert "Turn 1" in content, "Forbidden Actions must reference Turn 1."
        assert "read-only" in content.lower() or "Writing files" in content, (
            "Forbidden Actions must explicitly forbid writes in Turn 1."
        )

    def test_forbidden_actions_bans_mcp_writes_in_turn1(self):
        content = _content()
        # Must reference the kinds of MCP operations that are banned in Turn 1.
        # We check for at least one mutating MCP tool name.
        mutating_tools = (
            "vault_create", "vault_update", "vault_delete",
            "vault_patch", "vault_edit",
        )
        assert any(tool in content for tool in mutating_tools), (
            f"Forbidden Actions must call out MCP write tools by name — "
            f"none of {mutating_tools} are mentioned."
        )


class TestFixableVsEscalationListed:
    def test_auto_fixable_checks_listed(self):
        content = _content()
        # The auto-fixable table must mention the fix function names
        # from doctor_checks. setup_env_vars was removed in the T5 follow-up
        # because it was a structural no-op — env-var failures now escalate
        # to /secondbrain:init.
        fixable_fns = (
            "rebuild_manifest",
            "create_log_md",
            "setup_profile",
            "setup_vault_scaffolding",
            "write_vault_id",
        )
        missing = [fn for fn in fixable_fns if fn not in content]
        assert not missing, (
            f"Skill must list every auto-fixable fix function. "
            f"Missing: {missing}"
        )

    def test_env_var_checks_escalate_not_autofix(self):
        """OBSIDIAN_API_KEY / OBSIDIAN_MCP_PORT failures are NOT auto-fixable —
        setup_env_vars was dropped from the fixable table because doctor
        can't mint an API key or guess a port. The skill must escalate these
        to /secondbrain:init instead.
        """
        content = _content()
        # The fixable TABLE must not advertise setup_env_vars anymore.
        # (The phrase may still appear in prose, e.g. "setup_env_vars was
        # removed in..." but we don't allow it to sit under the fixable
        # header.) Split the SKILL body into sections at "**Auto-fixable"
        # and check the immediately following table block.
        fixable_section_start = content.find("**Auto-fixable")
        assert fixable_section_start != -1, "Auto-fixable section missing"
        escalation_section_start = content.find("**Escalation-only", fixable_section_start)
        assert escalation_section_start != -1, "Escalation-only section missing"
        fixable_table = content[fixable_section_start:escalation_section_start]
        assert "setup_env_vars" not in fixable_table, (
            "setup_env_vars should NOT be in the auto-fixable table — "
            "env-var failures escalate to /secondbrain:init"
        )
        # And the escalation table must mention both env vars.
        escalation_block = content[escalation_section_start:]
        assert "obsidian_api_key" in escalation_block, (
            "obsidian_api_key missing from escalation table"
        )
        assert "obsidian_mcp_port" in escalation_block, (
            "obsidian_mcp_port missing from escalation table"
        )

    def test_escalation_checks_listed(self):
        content = _content()
        # Things doctor CANNOT fix must be documented as manual escalations.
        assert "/plugin install" in content, (
            "Skill must escalate plugin install to /plugin install."
        )
        assert "install_git_hooks.py" in content, (
            "Skill must mention install_git_hooks.py for the core.hooksPath escalation."
        )


class TestNoStaleInitDispatch:
    def test_no_bulk_init_redirect(self):
        """Old doctor sent every failure to `/secondbrain:init`. That pattern
        conflicts with the two-turn flow — Phase 2 has specific fix functions
        per check, not a blanket init redirect.
        """
        content = _content()
        # We still allow "run /secondbrain:init" in the escalation table for
        # scheduled-task registration (which really does require init), but
        # the skill should NOT say "if anything is wrong, run /secondbrain:init".
        banned_phrases = (
            "If the user wants automatic repair, send them to `/secondbrain:init`",
            "always tell the user to run /secondbrain:init",
        )
        for phrase in banned_phrases:
            assert phrase not in content, (
                f"Skill contains stale blanket-init-redirect phrase: {phrase!r}. "
                "Each check has its own fix function now — don't blanket-redirect."
            )

    def test_read_only_badge_removed(self):
        """Old doctor described itself as 'Read-only — never modifies anything.'
        That's now wrong: doctor IS read-only in Phase 1, but Phase 2 can
        modify state. Update the description."""
        content = _content()
        assert "Read-only — never modifies anything" not in content, (
            "The description still says doctor is 'Read-only — never modifies "
            "anything.' but Phase 2 now fixes things on confirmation. Update."
        )


class TestChecksExistAndMatch:
    def test_check_names_mentioned(self):
        """Every check the Python engine runs should appear somewhere in the
        skill body — either by name or via its fix function name."""
        content = _content()
        # These are the check identifiers from doctor_checks.CheckResult.name
        check_names = (
            "obsidian_api_key",
            "obsidian_mcp_port",
            "manifest",
            "log_md",
            "profile",
            "standard_folders",
            "vault_identity_cross",
        )
        missing = [n for n in check_names if n not in content]
        assert not missing, (
            f"The skill body must reference every check the engine runs. "
            f"Missing: {missing}"
        )
