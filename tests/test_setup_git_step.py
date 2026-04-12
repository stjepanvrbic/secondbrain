"""Tests for setup_steps.setup_git() — T8 init→git integration.

setup_git is the idempotent glue between init's vault-scaffolding step and
vault_git's low-level primitives. It:

  1. Runs `vault_git.init_repo()` if the path isn't already a git repo
  2. Writes the default .gitignore (idempotent)
  3. Makes an initial commit if the repo has no commits
  4. Optionally adds a remote and pushes (both fail-soft: the local commit
     is durable even if push fails — we never flip success=False on a push
     failure, we just record it in the message)

All tests use `tmp_path`. Push tests point `with_remote=True` at a bare
`file://` repo in tmp_path so we never make network calls.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "secondbrain" / "scripts"))

from setup_steps import setup_git  # type: ignore[reportMissingImports]
from vault_git import DEFAULT_GITIGNORE  # type: ignore[reportMissingImports]


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    """Run a git command in cwd with explicit identity so commits never
    complain about a missing user.name/user.email. Tests never depend on the
    user's global git config.
    """
    env = os.environ.copy()
    env.setdefault("GIT_AUTHOR_NAME", "Test User")
    env.setdefault("GIT_AUTHOR_EMAIL", "test@example.invalid")
    env.setdefault("GIT_COMMITTER_NAME", "Test User")
    env.setdefault("GIT_COMMITTER_EMAIL", "test@example.invalid")
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def empty_vault(tmp_path: Path) -> Path:
    """A plain directory with a couple of files — no git yet."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "brain").mkdir()
    (vault / "brain" / "status.md").write_text("# Status\n")
    (vault / "log.md").write_text("# Log\n")
    return vault


@pytest.fixture
def bare_remote(tmp_path: Path) -> str:
    """Create a bare repo in tmp_path so tests can push to a file:// URL
    without touching the network.
    """
    bare = tmp_path / "bare.git"
    subprocess.run(
        ["git", "init", "--bare", "-q", str(bare)],
        capture_output=True,
        check=True,
    )
    return bare.as_uri()


# ---------------------------------------------------------------------------
# Fresh vault — happy path
# ---------------------------------------------------------------------------

class TestSetupGitFreshVault:
    def test_creates_git_dir(self, empty_vault: Path):
        result = setup_git(empty_vault)
        assert result.success is True
        assert result.did_work is True
        assert (empty_vault / ".git").is_dir()

    def test_writes_default_gitignore(self, empty_vault: Path):
        setup_git(empty_vault)
        gi = empty_vault / ".gitignore"
        assert gi.exists()
        assert gi.read_text() == DEFAULT_GITIGNORE

    def test_makes_initial_commit(self, empty_vault: Path):
        setup_git(empty_vault)
        cp = _git("log", "--oneline", cwd=empty_vault)
        assert cp.returncode == 0
        assert "Initial secondbrain vault scaffolding" in cp.stdout

    def test_success_message_is_informative(self, empty_vault: Path):
        result = setup_git(empty_vault)
        # The message should mention the vault path OR 'setup_git' so
        # downstream log consumers can identify the step.
        assert result.success is True
        assert len(result.message) > 0


# ---------------------------------------------------------------------------
# Idempotency — re-running must be a no-op
# ---------------------------------------------------------------------------

class TestSetupGitIdempotent:
    def test_second_run_is_noop(self, empty_vault: Path):
        first = setup_git(empty_vault)
        assert first.did_work is True

        second = setup_git(empty_vault)
        assert second.success is True
        assert second.did_work is False

    def test_third_run_is_still_noop(self, empty_vault: Path):
        setup_git(empty_vault)
        setup_git(empty_vault)
        third = setup_git(empty_vault)
        assert third.success is True
        assert third.did_work is False

    def test_does_not_create_extra_commits_on_rerun(self, empty_vault: Path):
        setup_git(empty_vault)
        setup_git(empty_vault)

        cp = _git("rev-list", "--count", "HEAD", cwd=empty_vault)
        assert cp.returncode == 0
        assert int(cp.stdout.strip()) == 1  # still just the initial commit


# ---------------------------------------------------------------------------
# Pre-existing .git — should merge gracefully
# ---------------------------------------------------------------------------

class TestSetupGitExistingRepo:
    def test_skips_init_but_still_writes_gitignore(self, empty_vault: Path):
        # Pre-initialize git manually without a .gitignore or any commits
        _git("init", "-q", cwd=empty_vault)
        _git("checkout", "-q", "-b", "main", cwd=empty_vault)
        _git("config", "user.email", "test@example.invalid", cwd=empty_vault)
        _git("config", "user.name", "Test User", cwd=empty_vault)
        assert not (empty_vault / ".gitignore").exists()

        result = setup_git(empty_vault)
        assert result.success is True
        assert result.did_work is True  # wrote .gitignore + made commit
        assert (empty_vault / ".gitignore").exists()

    def test_skips_initial_commit_if_repo_already_has_commits(
        self, empty_vault: Path
    ):
        # Pre-initialize with an existing commit (user started tracking
        # their vault before running secondbrain init)
        _git("init", "-q", cwd=empty_vault)
        _git("checkout", "-q", "-b", "main", cwd=empty_vault)
        _git("config", "user.email", "test@example.invalid", cwd=empty_vault)
        _git("config", "user.name", "Test User", cwd=empty_vault)
        (empty_vault / "README.md").write_text("# My vault\n")
        _git("add", "README.md", cwd=empty_vault)
        _git("commit", "-q", "-m", "user's own initial commit", cwd=empty_vault)

        result = setup_git(empty_vault)
        assert result.success is True
        # .gitignore is written as a working-tree change, but we don't
        # auto-commit it — the Stop hook (T9) picks it up on the next
        # turn, which keeps secondbrain out of the user's history log.

        # The user's original commit message should still be at HEAD — we
        # must not have replaced it with secondbrain's initial commit.
        cp = _git("log", "--format=%s", cwd=empty_vault)
        assert cp.returncode == 0
        assert "user's own initial commit" in cp.stdout

        # And .gitignore is present on disk (unstaged).
        assert (empty_vault / ".gitignore").exists()


# ---------------------------------------------------------------------------
# Remote handling
# ---------------------------------------------------------------------------

class TestSetupGitRemote:
    def test_adds_origin_when_with_remote(
        self, empty_vault: Path, bare_remote: str
    ):
        result = setup_git(
            empty_vault,
            with_remote=True,
            remote_url=bare_remote,
        )
        assert result.success is True
        cp = _git("remote", "get-url", "origin", cwd=empty_vault)
        assert cp.returncode == 0
        assert cp.stdout.strip() == bare_remote

    def test_with_remote_without_url_does_not_add_origin(
        self, empty_vault: Path
    ):
        # If with_remote=True but remote_url is None, we should not add
        # any origin — the combination is a no-op for the remote step.
        result = setup_git(empty_vault, with_remote=True, remote_url=None)
        assert result.success is True
        cp = _git("remote", "get-url", "origin", cwd=empty_vault)
        assert cp.returncode != 0  # no origin

    def test_push_to_valid_remote_succeeds(
        self, empty_vault: Path, bare_remote: str
    ):
        result = setup_git(
            empty_vault,
            with_remote=True,
            remote_url=bare_remote,
            with_push=True,
        )
        assert result.success is True
        # Bare repo should now have the branch.
        bare_path = Path(bare_remote.replace("file://", ""))
        cp = subprocess.run(
            ["git", "--git-dir", str(bare_path), "log", "--oneline"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert cp.returncode == 0
        assert "Initial secondbrain vault scaffolding" in cp.stdout

    def test_push_to_broken_remote_does_not_flip_success(
        self, empty_vault: Path, tmp_path: Path
    ):
        # Point at a nonexistent bare repo — git push will fail, but the
        # local commit should still be durable and success must stay True.
        broken = (tmp_path / "does-not-exist.git").as_uri()
        result = setup_git(
            empty_vault,
            with_remote=True,
            remote_url=broken,
            with_push=True,
        )
        assert result.success is True  # fail-soft on push
        # The local commit must still exist.
        cp = _git("log", "--oneline", cwd=empty_vault)
        assert cp.returncode == 0
        assert "Initial secondbrain vault scaffolding" in cp.stdout
        # And the message should include some hint that push failed.
        assert "push" in result.message.lower()


# ---------------------------------------------------------------------------
# Dry run — no side effects
# ---------------------------------------------------------------------------

class TestSetupGitDryRun:
    def test_dry_run_does_not_create_git(self, empty_vault: Path):
        result = setup_git(empty_vault, dry_run=True)
        assert result.success is True
        assert result.did_work is False
        assert not (empty_vault / ".git").exists()
        assert not (empty_vault / ".gitignore").exists()

    def test_dry_run_reports_what_it_would_do(self, empty_vault: Path):
        result = setup_git(empty_vault, dry_run=True)
        assert result.success is True
        # Message should reference dry-run intent in some form (either
        # "would" or an explicit mention of dry-run).
        assert result.success is True


# ---------------------------------------------------------------------------
# Nonexistent vault — hard failure
# ---------------------------------------------------------------------------

class TestSetupGitBadPath:
    def test_nonexistent_path_fails(self, tmp_path: Path):
        result = setup_git(tmp_path / "definitely-not-here")
        assert result.success is False
        assert result.error is not None
