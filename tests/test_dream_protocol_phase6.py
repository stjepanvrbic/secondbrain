"""String-contract tests for dream-protocol's new Phase 6 (T9).

Phase 6 — "Commit nightly state" — replaces the aspirational "git commit"
wording that used to live only in the scheduled-tasks description. The dream
skill now actually commits the nightly checkpoint by shelling out to
`vault_git.py commit-stop`.

These tests pin down the phase body so future refactors can't silently drop
the commit step or change its message in ways that break the nightly
checkpoint contract. They are deliberately string-level rather than behavior
tests: dream-protocol is a prompt-driven skill, and the Phase 6 body is
instructions for the agent, not executable code.

Scope:
    - "Phase 6" section header exists
    - References `vault_git.py commit-stop`
    - Has language about skipping silently if not a git repo (user opt-out)
    - Has error handling that logs to log.md rather than hard-failing
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


class TestPhase6Exists:
    def test_phase_6_section_header(self):
        content = _content()
        assert "Phase 6" in content, (
            "dream-protocol must have a Phase 6 section — the nightly git "
            "commit step that makes the scheduled-task description accurate."
        )

    def test_phase_6_is_after_phase_5(self):
        content = _content()
        p5 = content.find("Phase 5")
        p6 = content.find("Phase 6")
        assert p5 != -1, "Phase 5 must exist"
        assert p6 != -1, "Phase 6 must exist"
        assert p6 > p5, (
            "Phase 6 must appear AFTER Phase 5 in the skill body — the "
            "commit is the last step of the nightly run so log.md gets "
            "included in the commit."
        )

    def test_phase_6_mentions_commit(self):
        content = _content()
        # Extract the Phase 6 section (everything from "Phase 6" up to the
        # next top-level section or end of file) and check it mentions the
        # commit verb.
        start = content.find("Phase 6")
        # Next top-level "#" heading after phase 6
        rest = content[start:]
        heading_idx = rest.find("\n# ", 10)
        phase_6 = rest if heading_idx == -1 else rest[:heading_idx]
        assert "commit" in phase_6.lower(), (
            "Phase 6 body must mention 'commit' — the whole point of the "
            "phase is to create a nightly checkpoint commit."
        )


class TestPhase6VaultGitReference:
    def test_phase_6_references_vault_git_py(self):
        content = _content()
        start = content.find("Phase 6")
        rest = content[start:]
        heading_idx = rest.find("\n# ", 10)
        phase_6 = rest if heading_idx == -1 else rest[:heading_idx]
        assert "vault_git.py" in phase_6, (
            "Phase 6 must reference vault_git.py — it's the canonical "
            "CLI for vault git operations."
        )

    def test_phase_6_references_commit_stop_subcommand(self):
        content = _content()
        start = content.find("Phase 6")
        rest = content[start:]
        heading_idx = rest.find("\n# ", 10)
        phase_6 = rest if heading_idx == -1 else rest[:heading_idx]
        assert "commit-stop" in phase_6, (
            "Phase 6 must invoke the `commit-stop` subcommand specifically "
            "(not `init`, not `reset-last-commit`). This is the same "
            "command the Stop hook uses, so the behavior is consistent."
        )


class TestPhase6SkipSilentlyWhenNoGit:
    def test_phase_6_documents_skip_when_not_git_repo(self):
        """If the user opted out of git at init time, Phase 6 must skip
        silently — not fail, not prompt, not log an error. Git is opt-in.
        """
        content = _content()
        start = content.find("Phase 6")
        rest = content[start:]
        heading_idx = rest.find("\n# ", 10)
        phase_6 = rest if heading_idx == -1 else rest[:heading_idx]
        # At least one of these phrases should be present.
        skip_phrases = [
            "skip silently",
            "skip",
            "opted out",
            "not under git",
            "not a git repo",
        ]
        assert any(phrase in phase_6.lower() for phrase in skip_phrases), (
            "Phase 6 must document what happens when the vault isn't a "
            "git repo (user opted out) — the expected behavior is to "
            "skip silently rather than fail the nightly run."
        )


class TestPhase6ErrorHandling:
    def test_phase_6_never_hard_fails(self):
        """dream-protocol should never hard-fail on a commit error — the
        vault content is already on disk from earlier phases, so a commit
        failure is a soft failure that gets logged, not propagated.
        """
        content = _content()
        start = content.find("Phase 6")
        rest = content[start:]
        heading_idx = rest.find("\n# ", 10)
        phase_6 = rest if heading_idx == -1 else rest[:heading_idx]
        # Either the text explicitly says "never hard-fail" / "continue" /
        # "log" + error, or the phrase "should not fail" appears. Keep the
        # check flexible so we're not too brittle on exact wording.
        fail_soft_phrases = [
            "continue",
            "log the error",
            "never hard-fail",
            "not hard-fail",
            "never hard fail",
            "log.md",
        ]
        assert any(phrase in phase_6.lower() for phrase in fail_soft_phrases), (
            "Phase 6 must document fail-soft behavior: a commit error "
            "should be logged to log.md and the run should continue, "
            "not propagate the failure."
        )
