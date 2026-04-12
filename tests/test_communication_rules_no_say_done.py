"""Regression: communication-rules.md must NOT tell the agent to
interpret 'done'/'bye'/'goodnight' as session-end triggers (T14).

Pre-T14, an older "say done" instruction lived in the communication
rules — it told the agent that the user saying "done" was a signal to
run /secondbrain:session-end. That behavior is now owned by the Stop
hook (per-turn ingest) and session-end.sh (audit logs), so the rule
is dead weight and actively contradicts the new architecture:

    - The Stop hook commits and ingests after every turn, so "done"
      doesn't need to trigger anything special.
    - session-end.sh runs on real session end (tab close, timeout,
      etc.) — it's not a user-visible command anymore.

This test is a forward guard: if someone adds a "say done" instruction
back to communication-rules.md in the future (for any reason), the
suite fails and the reviewer sees the conflict.

Scope:
    - The phrase `say done` / `says "done"` / `user says done` in the
      session-end-trigger sense is absent from communication-rules.md.
    - The word `done` in the routing-trigger sense is absent — but the
      word itself may still appear in the prose (e.g. "one thing done
      well") so we check for trigger-shaped phrasing rather than the
      literal word.
"""

from __future__ import annotations

from pathlib import Path

RULES_PATH = (
    Path(__file__).resolve().parent.parent
    / "secondbrain"
    / "references"
    / "communication-rules.md"
)


def _rules() -> str:
    return RULES_PATH.read_text()


def _rules_lower() -> str:
    return _rules().lower()


class TestRulesFileExists:
    def test_file_exists(self):
        assert RULES_PATH.is_file(), (
            f"communication-rules.md must exist at {RULES_PATH}"
        )


class TestNoSayDoneTrigger:
    def test_no_say_done_phrase(self):
        """The literal phrase 'say done' must not appear — it was the
        most direct form of the old trigger instruction.
        """
        assert "say done" not in _rules_lower(), (
            "communication-rules.md must not tell the agent to interpret "
            "the user saying 'done' as a session-end trigger — the Stop "
            "hook handles per-turn state now."
        )

    def test_no_says_done_phrase(self):
        low = _rules_lower()
        # Guards against `says "done"`, `says 'done'`, `says done`.
        for variant in ('says "done"', "says 'done'", "says done"):
            assert variant not in low, (
                f"communication-rules.md must not instruct the agent to "
                f"react to `{variant}` — session-end triggers moved to "
                f"the Stop hook in T13/T14."
            )

    def test_no_session_end_trigger_instruction(self):
        """The broader old pattern: 'when the user says X, run
        /secondbrain:session-end'. We check for the specific slash
        command as the session-end-trigger tell.
        """
        low = _rules_lower()
        assert "/secondbrain:session-end" not in low, (
            "communication-rules.md must not reference /secondbrain:session-end — "
            "that skill was retired in T13 when the Stop hook took over "
            "per-turn ingest."
        )

    def test_no_goodnight_trigger(self):
        low = _rules_lower()
        # Specifically check that "goodnight" is not paired with any
        # trigger language. A stray literary "goodnight" in prose is
        # fine, but the combination with "trigger" / "session" /
        # "invoke" / "run" is a red flag.
        if "goodnight" in low:
            # Find the index and look at ±80 characters.
            idx = low.find("goodnight")
            window = low[max(0, idx - 80) : idx + 80]
            forbidden = ("trigger", "session-end", "invoke", "run /")
            assert not any(word in window for word in forbidden), (
                "communication-rules.md must not use 'goodnight' as a "
                "session-end trigger."
            )


class TestRestOfFilePreserved:
    def test_forbidden_section_intact(self):
        """Sanity: the 'Forbidden' section still exists — we should
        only have removed a trigger instruction, not gutted the file.
        """
        assert "## Forbidden" in _rules(), (
            "removing the 'say done' instruction should not have "
            "deleted the whole Forbidden section."
        )

    def test_required_section_intact(self):
        """Sanity: the 'Required' section still exists."""
        assert "## Required" in _rules()
