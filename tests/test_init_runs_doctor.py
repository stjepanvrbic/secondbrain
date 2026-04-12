"""String-contract tests for secondbrain/skills/init/SKILL.md — T6.

These are regression guards, not behavior tests. They pin down the
invariant that init must run doctor at the END of its flow and gate
"Setup complete!" on doctor reporting a clean state. This closes the
Phase 1 "init leaves vault in verified state" loop — without these
checks, init can claim success while the vault is still unhealthy.

The contract:
1. The skill invokes `doctor_cli.py --diagnose` as part of its final step.
2. The skill invokes `doctor_cli.py --treat` (in interactive mode) when
   diagnose reports failures.
3. The skill gates its "Setup complete" / "healthy" message on doctor
   reporting a clean state.
4. The skill has an explicit forbidden action around claiming setup
   completion while doctor reports any non-skip failures.
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


def _content() -> str:
    return SKILL_PATH.read_text()


class TestDoctorDiagnoseReferenced:
    def test_skill_references_doctor_cli_diagnose(self):
        content = _content()
        assert "doctor_cli.py --diagnose" in content, (
            "The init skill's final verification step must invoke "
            "doctor_cli.py --diagnose — that's how init confirms the vault "
            "is healthy before claiming setup is complete."
        )

    def test_skill_references_doctor_cli_treat(self):
        content = _content()
        assert "doctor_cli.py --treat" in content, (
            "The init skill must invoke doctor_cli.py --treat when diagnose "
            "reports fixable failures, so init can actually close the "
            "health loop instead of just reporting issues."
        )

    def test_treat_invocation_uses_interactive(self):
        content = _content()
        # The treat invocation should pass --interactive so profile seeding
        # and similar interactive fixes can prompt the user. Check that
        # --interactive appears alongside --treat somewhere in the skill.
        treat_index = content.find("doctor_cli.py --treat")
        assert treat_index >= 0
        # Look in the same section (within a few hundred chars).
        nearby = content[treat_index : treat_index + 400]
        assert "--interactive" in nearby, (
            "The init skill's doctor treat invocation must pass --interactive "
            "so interactive fixes (profile seeding) can prompt the user."
        )


class TestSetupCompleteGatedOnDoctor:
    def test_skill_mentions_setup_complete_gating(self):
        content = _content()
        # The skill must say something like "only print Setup complete when
        # doctor reports clean" or equivalent gating language.
        has_gating = (
            "doctor reports clean" in content
            or "doctor reports no failures" in content
            or "doctor reports zero failures" in content
            or "only if doctor" in content.lower()
        )
        assert has_gating, (
            "The init skill must explicitly gate its 'Setup complete' "
            "message on doctor reporting a clean state. Without this "
            "gating language, the skill can regress to claiming success "
            "while the vault is still unhealthy."
        )

    def test_forbidden_action_covers_claiming_complete_on_failure(self):
        content = _content()
        # The skill's Forbidden Actions section must include language
        # forbidding a "setup complete" claim when doctor reports failures.
        forbidden_index = content.find("Forbidden Actions")
        assert forbidden_index >= 0, (
            "The init skill must have a Forbidden Actions section."
        )
        forbidden_section = content[forbidden_index:]
        has_forbidden_claim = (
            "claiming setup is complete" in forbidden_section.lower()
            or "claim setup is complete" in forbidden_section.lower()
            or "claim setup complete" in forbidden_section.lower()
            or "setup is complete when doctor" in forbidden_section.lower()
        )
        assert has_forbidden_claim, (
            "The Forbidden Actions section must explicitly forbid claiming "
            "setup is complete when doctor reports any non-skip failures."
        )


class TestFinalVerificationStepExists:
    def test_final_step_has_doctor_header(self):
        content = _content()
        # There should be a section specifically framing doctor as the
        # final verification gate. Require "Final verification via doctor"
        # (or with a dash) as a section header — don't accept the
        # pre-existing "Final verification" language from Step 7d, which
        # refers to verify_vault.py, not doctor.
        has_final_section = (
            "Final verification via doctor" in content
            or "Final verification — doctor" in content
            or "Final verification - doctor" in content
        )
        assert has_final_section, (
            "The init skill must have a section titled 'Final verification "
            "via doctor' (or equivalent) that explicitly runs doctor at "
            "the end of init."
        )
