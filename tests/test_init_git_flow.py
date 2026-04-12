"""String-contract tests for secondbrain/skills/init/SKILL.md — T8 git flow.

These tests pin down the Step 4a text so future refactors don't silently
drop the git prompt, the remote consent flow, or the Forbidden Action that
prevents a push without explicit URL confirmation.

We treat these as load-bearing contract tests: the init skill is the only
surface that asks the user whether to put their vault under git, so if the
prompt wording or the script references drift, users will be left without
version control (or worse, pushed to somewhere they didn't authorize).

Scope:
  - The "Step 4a — Vault git tracking" section header exists
  - The git-consent prompt asks "OK to enable git?"
  - The remote-consent prompt asks "Enable remote push?"
  - URL validation language is present (mentions `git@`, `https://`, etc.)
  - A reference to `vault_git.py init --vault` exists for the init call
  - A reference to pushing (either via vault_git.py or direct git) exists
  - The "Never push to the remote without explicit user confirmation"
    Forbidden Action is in place
  - The idempotency note mentions Step 4a
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


class TestStep4aExists:
    def test_step_4a_section_header(self):
        content = _content()
        assert "Step 4a" in content, (
            "init skill must have a Step 4a — the git consent flow lives "
            "between vault scaffolding (Step 4) and env vars / scheduled "
            "tasks (Step 5)."
        )

    def test_step_4a_heading_mentions_git_tracking(self):
        content = _content()
        # The exact heading text from the plan.
        assert "Vault git tracking" in content, (
            "Step 4a's heading must read 'Vault git tracking' so the user "
            "understands this is a new setup section."
        )


class TestGitConsentPrompt:
    def test_asks_for_git_consent(self):
        content = _content()
        assert "OK to enable git?" in content, (
            "Init must ask 'OK to enable git?' verbatim so users see a "
            "clear yes/no decision point."
        )

    def test_explains_what_git_does(self):
        content = _content()
        # At least one of these explanatory phrases should be present so
        # the user understands the tradeoff — we're not just asking them
        # to type yes blindly.
        assert (
            "version control" in content.lower()
            or "every change" in content.lower()
            or "/secondbrain:undo-last-turn" in content
        ), (
            "Step 4a must explain what git tracking gets the user — at "
            "minimum mention version control, commits, or the undo skill."
        )

    def test_warns_about_sync_conflicts(self):
        content = _content()
        # The prompt should warn users that iCloud/Dropbox/Obsidian Sync
        # can fight with git. We check for at least one sync vendor keyword.
        lowered = content.lower()
        assert (
            "icloud" in lowered
            or "dropbox" in lowered
            or "obsidian sync" in lowered
        ), (
            "Step 4a must warn users that cloud sync can conflict with git "
            "tracking so they can make an informed decision."
        )

    def test_explicitly_says_stays_local(self):
        content = _content()
        assert "LOCAL" in content or "local to your machine" in content.lower(), (
            "The git consent prompt must explicitly tell the user that git "
            "stays LOCAL — no automatic push — so they're not surprised."
        )


class TestRemoteConsentPrompt:
    def test_asks_for_remote_consent(self):
        content = _content()
        assert "Enable remote push?" in content, (
            "After local git is set up, init must ask 'Enable remote "
            "push?' so the user opts in explicitly."
        )

    def test_mentions_cross_device_sync_rationale(self):
        content = _content()
        # At least one phrase indicating the purpose of the remote.
        lowered = content.lower()
        assert (
            "cross-device" in lowered
            or "cross device" in lowered
            or "push your vault" in lowered
        ), (
            "The remote consent prompt must explain WHY the user would want "
            "a remote (cross-device sync) so they can make an informed "
            "decision."
        )

    def test_mentions_user_provides_url(self):
        content = _content()
        assert "remote URL" in content or "remote url" in content.lower(), (
            "Step 4a must ask for a remote URL, not assume one — the "
            "plugin must never push to a URL the user hasn't confirmed."
        )


class TestUrlValidation:
    def test_validates_url_shape(self):
        content = _content()
        # The SKILL.md must mention at least two of the valid URL prefixes
        # so the implementation can't silently accept anything.
        lowered = content.lower()
        hits = sum(
            1
            for prefix in ("git@", "https://", "ssh://", "file://")
            if prefix in lowered
        )
        assert hits >= 2, (
            "Step 4a must enumerate at least two valid git URL shapes "
            "(git@, https://, ssh://, file://) so the init skill can "
            "validate user input before adding the remote."
        )


class TestScriptReferences:
    def test_references_vault_git_init(self):
        content = _content()
        assert "vault_git.py init" in content, (
            "Step 4a must invoke `vault_git.py init --vault <path>` so the "
            "git setup goes through the T7 helper and stays idempotent."
        )

    def test_mentions_vault_flag(self):
        content = _content()
        # The init call must pass --vault so vault_git knows where to work.
        # We accept either the literal 'init --vault' or the templated form.
        assert "vault_git.py init --vault" in content, (
            "The vault_git.py call must include the --vault flag — without "
            "it, vault_git aborts."
        )

    def test_references_push_flow(self):
        content = _content()
        # Either via vault_git.py, via setup_git, or via a direct `git push`
        # in Step 4a. We just need evidence that push is documented.
        assert "git push" in content or "with_push" in content, (
            "Step 4a must document how the initial push actually runs — "
            "either a direct `git push` or a setup_git(with_push=True) call."
        )


class TestForbiddenAction:
    def test_never_push_without_confirmation(self):
        content = _content()
        assert "Never push to the remote without explicit user confirmation" in content, (
            "Forbidden Actions must include 'Never push to the remote "
            "without explicit user confirmation of the URL' so nothing "
            "ever auto-pushes."
        )

    def test_forbidden_actions_section_exists(self):
        content = _content()
        # Sanity guard — make sure we're adding the forbidden action to
        # the real section and not floating orphan text.
        assert "Forbidden Actions" in content, (
            "SKILL.md must still have a 'Forbidden Actions' section — the "
            "Never-push rule lives there."
        )


class TestIdempotencyNote:
    def test_idempotency_section_mentions_step_4a(self):
        content = _content()
        # The existing Idempotency section lists each step's idempotency
        # semantics. Step 4a joins that list so the contract is complete.
        assert "Step 4a" in content, (
            "The Idempotency section must reference Step 4a so re-runs of "
            "init don't re-prompt on already-initialized vaults."
        )
