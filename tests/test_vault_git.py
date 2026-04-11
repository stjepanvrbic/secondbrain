"""Tests for vault_git.py — git operations on the user's Obsidian vault.

This module is the foundation of Phase 2 (lifecycle redesign):
  - T7 (this task): stdlib-only git helper + CLI entry point
  - T8: init flow integrates it with user consent
  - T9: Stop hook calls `vault_git.py commit-stop` after every agent turn

CRITICAL: tests operate on the *vault*, never on the secondbrain repo itself.
Every test uses `tmp_path` for a fresh git repo. No network calls — any push
test uses `file://` URLs to a bare repo in tmp_path.

Tests exercise real `git` subprocesses (no mocking) because the whole point
of this module is to get the git CLI invocations right. Mocking would make
the tests trivial and worthless.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import List

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "secondbrain" / "scripts"))

from vault_git import (  # type: ignore[reportMissingImports]
    Commit,
    DEFAULT_AUTHOR,
    DEFAULT_GITIGNORE,
    commit_changes,
    files_changed_in_last_commit,
    has_uncommitted_changes,
    init_repo,
    initial_commit,
    is_git_repo,
    list_recent_commits,
    reset_last_commit,
    write_gitignore,
)

VAULT_GIT_SCRIPT = (
    Path(__file__).resolve().parent.parent / "secondbrain" / "scripts" / "vault_git.py"
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    """Run a git command in cwd with explicit identity so the commit hooks
    never complain about a missing user.name/user.email. Returns the CP so
    tests can assert on returncode/stdout as needed.
    """
    env = os.environ.copy()
    # Make absolutely sure the test's git sees no global config leaking in.
    env["GIT_AUTHOR_NAME"] = env.get("GIT_AUTHOR_NAME", "Test User")
    env["GIT_AUTHOR_EMAIL"] = env.get("GIT_AUTHOR_EMAIL", "test@example.invalid")
    env["GIT_COMMITTER_NAME"] = env.get("GIT_COMMITTER_NAME", "Test User")
    env["GIT_COMMITTER_EMAIL"] = env.get("GIT_COMMITTER_EMAIL", "test@example.invalid")
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def fresh_repo(tmp_path: Path) -> Path:
    """An empty directory with `git init` already run and an initial dummy
    file staged+committed so tests don't have to bootstrap each time.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    _git("init", "-q", cwd=vault)
    # Give the repo a deterministic default branch so tests don't break on
    # whichever init.defaultBranch the user's global config prefers.
    _git("checkout", "-q", "-b", "main", cwd=vault)
    # Must write to the git config so subsequent commits don't fail on
    # missing identity even if test env vars get stripped.
    _git("config", "user.email", "test@example.invalid", cwd=vault)
    _git("config", "user.name", "Test User", cwd=vault)
    (vault / "seed.md").write_text("# seed\n")
    _git("add", "seed.md", cwd=vault)
    _git("commit", "-q", "-m", "seed commit", cwd=vault)
    return vault


@pytest.fixture
def empty_vault(tmp_path: Path) -> Path:
    """A directory that is NOT a git repo yet."""
    vault = tmp_path / "empty-vault"
    vault.mkdir()
    (vault / "brain").mkdir()
    (vault / "brain" / "status.md").write_text("# Status\n")
    (vault / "log.md").write_text("# Log\n")
    return vault


# ===========================================================================
# is_git_repo
# ===========================================================================

class TestIsGitRepo:
    def test_fresh_dir_is_not_a_repo(self, empty_vault: Path):
        assert is_git_repo(empty_vault) is False

    def test_after_git_init_is_a_repo(self, empty_vault: Path):
        _git("init", "-q", cwd=empty_vault)
        assert is_git_repo(empty_vault) is True

    def test_file_is_not_a_repo(self, tmp_path: Path):
        f = tmp_path / "file.md"
        f.write_text("not a dir")
        assert is_git_repo(f) is False

    def test_nonexistent_path_is_not_a_repo(self, tmp_path: Path):
        assert is_git_repo(tmp_path / "nope") is False


# ===========================================================================
# init_repo
# ===========================================================================

class TestInitRepo:
    def test_fresh_dir_creates_git_dir(self, empty_vault: Path):
        result = init_repo(empty_vault)
        assert result.success is True
        assert result.did_work is True
        assert (empty_vault / ".git").is_dir()

    def test_idempotent_on_second_call(self, empty_vault: Path):
        init_repo(empty_vault)
        result = init_repo(empty_vault)
        assert result.success is True
        assert result.did_work is False

    def test_with_remote_adds_origin(self, empty_vault: Path, tmp_path: Path):
        bare = tmp_path / "bare.git"
        subprocess.run(
            ["git", "init", "--bare", "-q", str(bare)],
            capture_output=True,
            check=True,
        )
        remote_url = bare.as_uri()
        result = init_repo(empty_vault, with_remote=True, remote_url=remote_url)
        assert result.success is True
        assert result.did_work is True
        cp = _git("remote", "get-url", "origin", cwd=empty_vault)
        assert cp.returncode == 0
        assert cp.stdout.strip() == remote_url

    def test_nonexistent_vault_fails(self, tmp_path: Path):
        result = init_repo(tmp_path / "nope")
        assert result.success is False
        assert result.error is not None

    def test_dry_run_does_not_create_git_dir(self, empty_vault: Path):
        result = init_repo(empty_vault, dry_run=True)
        assert result.success is True
        assert result.did_work is False
        assert not (empty_vault / ".git").exists()


# ===========================================================================
# write_gitignore
# ===========================================================================

class TestWriteGitignore:
    def test_fresh_repo_creates_gitignore(self, empty_vault: Path):
        _git("init", "-q", cwd=empty_vault)
        result = write_gitignore(empty_vault)
        assert result.success is True
        assert result.did_work is True
        gi = empty_vault / ".gitignore"
        assert gi.exists()
        assert gi.read_text() == DEFAULT_GITIGNORE

    def test_idempotent_when_already_correct(self, empty_vault: Path):
        _git("init", "-q", cwd=empty_vault)
        (empty_vault / ".gitignore").write_text(DEFAULT_GITIGNORE)
        result = write_gitignore(empty_vault)
        assert result.success is True
        assert result.did_work is False

    def test_replaces_different_content(self, empty_vault: Path):
        _git("init", "-q", cwd=empty_vault)
        (empty_vault / ".gitignore").write_text("# user custom\n*.log\n")
        result = write_gitignore(empty_vault)
        assert result.success is True
        assert result.did_work is True
        assert (empty_vault / ".gitignore").read_text() == DEFAULT_GITIGNORE

    def test_nonexistent_vault_fails(self, tmp_path: Path):
        result = write_gitignore(tmp_path / "nope")
        assert result.success is False

    def test_dry_run_does_not_write(self, empty_vault: Path):
        _git("init", "-q", cwd=empty_vault)
        result = write_gitignore(empty_vault, dry_run=True)
        assert result.success is True
        assert result.did_work is False
        assert not (empty_vault / ".gitignore").exists()


# ===========================================================================
# initial_commit
# ===========================================================================

class TestInitialCommit:
    def test_fresh_repo_commits_files(self, empty_vault: Path):
        _git("init", "-q", cwd=empty_vault)
        _git("checkout", "-q", "-b", "main", cwd=empty_vault)
        (empty_vault / ".gitignore").write_text(DEFAULT_GITIGNORE)
        (empty_vault / "log.md").write_text("# Log\n")
        result = initial_commit(empty_vault)
        assert result.success is True
        assert result.did_work is True

        cp = _git("log", "--oneline", cwd=empty_vault)
        assert cp.returncode == 0
        assert "Initial secondbrain vault scaffolding" in cp.stdout

    def test_idempotent_when_commits_exist(self, fresh_repo: Path):
        result = initial_commit(fresh_repo)
        assert result.success is True
        assert result.did_work is False

    def test_uses_default_author(self, empty_vault: Path):
        _git("init", "-q", cwd=empty_vault)
        _git("checkout", "-q", "-b", "main", cwd=empty_vault)
        (empty_vault / "log.md").write_text("# Log\n")
        initial_commit(empty_vault)
        cp = _git("log", "-1", "--format=%an <%ae>", cwd=empty_vault)
        assert cp.returncode == 0
        assert cp.stdout.strip() == DEFAULT_AUTHOR

    def test_custom_author_override(self, empty_vault: Path):
        _git("init", "-q", cwd=empty_vault)
        _git("checkout", "-q", "-b", "main", cwd=empty_vault)
        (empty_vault / "log.md").write_text("# Log\n")
        custom = "Custom Author <custom@example.invalid>"
        initial_commit(empty_vault, author=custom)
        cp = _git("log", "-1", "--format=%an <%ae>", cwd=empty_vault)
        assert cp.stdout.strip() == custom

    def test_commit_message_is_canonical(self, empty_vault: Path):
        _git("init", "-q", cwd=empty_vault)
        _git("checkout", "-q", "-b", "main", cwd=empty_vault)
        (empty_vault / "log.md").write_text("# Log\n")
        initial_commit(empty_vault)
        cp = _git("log", "-1", "--format=%s", cwd=empty_vault)
        assert cp.stdout.strip() == "Initial secondbrain vault scaffolding"

    def test_nonexistent_vault_fails(self, tmp_path: Path):
        result = initial_commit(tmp_path / "nope")
        assert result.success is False

    def test_not_a_repo_fails(self, empty_vault: Path):
        result = initial_commit(empty_vault)
        assert result.success is False
        assert result.error is not None

    def test_dry_run_does_not_commit(self, empty_vault: Path):
        _git("init", "-q", cwd=empty_vault)
        _git("checkout", "-q", "-b", "main", cwd=empty_vault)
        (empty_vault / "log.md").write_text("# Log\n")
        result = initial_commit(empty_vault, dry_run=True)
        assert result.success is True
        assert result.did_work is False
        # Verify no commits exist
        cp = _git("log", "--oneline", cwd=empty_vault)
        # In an empty repo `git log` fails with code 128
        assert cp.returncode != 0 or cp.stdout.strip() == ""


# ===========================================================================
# has_uncommitted_changes
# ===========================================================================

class TestHasUncommittedChanges:
    def test_clean_repo(self, fresh_repo: Path):
        assert has_uncommitted_changes(fresh_repo) is False

    def test_untracked_file(self, fresh_repo: Path):
        (fresh_repo / "new.md").write_text("# new\n")
        assert has_uncommitted_changes(fresh_repo) is True

    def test_staged_file(self, fresh_repo: Path):
        (fresh_repo / "new.md").write_text("# new\n")
        _git("add", "new.md", cwd=fresh_repo)
        assert has_uncommitted_changes(fresh_repo) is True

    def test_modified_committed_file(self, fresh_repo: Path):
        (fresh_repo / "seed.md").write_text("# seed\n\nnew content\n")
        assert has_uncommitted_changes(fresh_repo) is True

    def test_after_commit(self, fresh_repo: Path):
        (fresh_repo / "new.md").write_text("# new\n")
        _git("add", "new.md", cwd=fresh_repo)
        _git("commit", "-q", "-m", "add new", cwd=fresh_repo)
        assert has_uncommitted_changes(fresh_repo) is False

    def test_nonexistent_vault(self, tmp_path: Path):
        # Cannot raise — public API must tolerate bad paths.
        assert has_uncommitted_changes(tmp_path / "nope") is False


# ===========================================================================
# commit_changes
# ===========================================================================

class TestCommitChanges:
    def test_commits_modified_file(self, fresh_repo: Path):
        (fresh_repo / "new.md").write_text("# new\n")
        result = commit_changes(fresh_repo, "add new note")
        assert result.success is True
        assert result.did_work is True

        cp = _git("log", "-1", "--format=%s", cwd=fresh_repo)
        assert cp.stdout.strip() == "add new note"

    def test_no_changes_is_noop(self, fresh_repo: Path):
        result = commit_changes(fresh_repo, "nothing")
        assert result.success is True
        assert result.did_work is False

    def test_default_author(self, fresh_repo: Path):
        (fresh_repo / "new.md").write_text("# new\n")
        commit_changes(fresh_repo, "test")
        cp = _git("log", "-1", "--format=%an <%ae>", cwd=fresh_repo)
        assert cp.stdout.strip() == DEFAULT_AUTHOR

    def test_custom_author(self, fresh_repo: Path):
        (fresh_repo / "new.md").write_text("# new\n")
        custom = "Alice <alice@example.invalid>"
        commit_changes(fresh_repo, "test", author=custom)
        cp = _git("log", "-1", "--format=%an <%ae>", cwd=fresh_repo)
        assert cp.stdout.strip() == custom

    def test_push_no_remote_succeeds_but_reports_push_failure(
        self, fresh_repo: Path
    ):
        (fresh_repo / "new.md").write_text("# new\n")
        result = commit_changes(fresh_repo, "test", push=True)
        # Commit itself succeeds
        assert result.success is True
        assert result.did_work is True
        # Message mentions push failure
        assert "push" in result.message.lower()

    def test_push_with_remote_succeeds(self, fresh_repo: Path, tmp_path: Path):
        bare = tmp_path / "bare.git"
        # `git init -c init.defaultBranch=main --bare ...` works on any git
        # 2.x since `-c` is older than `--initial-branch`. This keeps the
        # test portable across whichever git the CI image has installed.
        subprocess.run(
            ["git", "-c", "init.defaultBranch=main", "init", "--bare", "-q", str(bare)],
            capture_output=True,
            check=True,
        )
        remote_url = bare.as_uri()
        _git("remote", "add", "origin", remote_url, cwd=fresh_repo)
        _git("push", "-q", "-u", "origin", "main", cwd=fresh_repo)

        (fresh_repo / "new.md").write_text("# new\n")
        result = commit_changes(fresh_repo, "push test", push=True)
        assert result.success is True
        assert result.did_work is True
        # The bare repo should now have the new commit. Query the main ref
        # explicitly since the bare repo's HEAD may differ from whichever
        # branch we pushed.
        cp = subprocess.run(
            ["git", "-C", str(bare), "log", "-1", "--format=%s", "main"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert cp.stdout.strip() == "push test"

    def test_nonexistent_vault_fails(self, tmp_path: Path):
        result = commit_changes(tmp_path / "nope", "test")
        assert result.success is False

    def test_not_a_repo_fails(self, empty_vault: Path):
        result = commit_changes(empty_vault, "test")
        assert result.success is False
        assert result.error is not None

    def test_dry_run_does_not_commit(self, fresh_repo: Path):
        (fresh_repo / "new.md").write_text("# new\n")
        result = commit_changes(fresh_repo, "test", dry_run=True)
        assert result.success is True
        assert result.did_work is False
        cp = _git("log", "-1", "--format=%s", cwd=fresh_repo)
        assert cp.stdout.strip() == "seed commit"


# ===========================================================================
# list_recent_commits
# ===========================================================================

class TestListRecentCommits:
    def test_single_commit(self, fresh_repo: Path):
        commits = list_recent_commits(fresh_repo)
        assert len(commits) == 1
        assert commits[0].message == "seed commit"
        assert commits[0].sha  # non-empty
        assert commits[0].author  # non-empty
        assert commits[0].date  # non-empty

    def test_multiple_commits_reverse_chronological(self, fresh_repo: Path):
        (fresh_repo / "a.md").write_text("a")
        _git("add", "a.md", cwd=fresh_repo)
        _git("commit", "-q", "-m", "add a", cwd=fresh_repo)

        (fresh_repo / "b.md").write_text("b")
        _git("add", "b.md", cwd=fresh_repo)
        _git("commit", "-q", "-m", "add b", cwd=fresh_repo)

        commits = list_recent_commits(fresh_repo)
        assert len(commits) == 3
        # Most recent first
        assert commits[0].message == "add b"
        assert commits[1].message == "add a"
        assert commits[2].message == "seed commit"

    def test_n_limit(self, fresh_repo: Path):
        (fresh_repo / "a.md").write_text("a")
        _git("add", "a.md", cwd=fresh_repo)
        _git("commit", "-q", "-m", "add a", cwd=fresh_repo)

        commits = list_recent_commits(fresh_repo, n=1)
        assert len(commits) == 1
        assert commits[0].message == "add a"

    def test_empty_repo_returns_empty_list(self, empty_vault: Path):
        _git("init", "-q", cwd=empty_vault)
        commits = list_recent_commits(empty_vault)
        assert commits == []

    def test_commit_shape(self, fresh_repo: Path):
        commits = list_recent_commits(fresh_repo)
        c = commits[0]
        assert isinstance(c, Commit)
        # SHA is 40 hex chars (or at least stable and non-empty)
        assert len(c.sha) >= 7
        # Date contains a digit (ISO format)
        assert any(ch.isdigit() for ch in c.date)

    def test_nonexistent_vault_returns_empty(self, tmp_path: Path):
        assert list_recent_commits(tmp_path / "nope") == []


# ===========================================================================
# reset_last_commit
# ===========================================================================

class TestResetLastCommit:
    def test_two_commits_resets_to_first(self, fresh_repo: Path):
        (fresh_repo / "new.md").write_text("# new\n")
        _git("add", "new.md", cwd=fresh_repo)
        _git("commit", "-q", "-m", "add new", cwd=fresh_repo)

        assert (fresh_repo / "new.md").exists()

        result = reset_last_commit(fresh_repo)
        assert result.success is True
        assert result.did_work is True

        # Working tree should be back at the seed commit.
        cp = _git("log", "--oneline", cwd=fresh_repo)
        assert cp.stdout.strip().count("\n") == 0  # one line
        assert "seed commit" in cp.stdout
        # Hard reset discards the file.
        assert not (fresh_repo / "new.md").exists()

    def test_mixed_reset_keeps_working_tree(self, fresh_repo: Path):
        (fresh_repo / "new.md").write_text("# new\n")
        _git("add", "new.md", cwd=fresh_repo)
        _git("commit", "-q", "-m", "add new", cwd=fresh_repo)

        result = reset_last_commit(fresh_repo, hard=False)
        assert result.success is True
        assert result.did_work is True
        # File should still exist in the working tree but be untracked.
        assert (fresh_repo / "new.md").exists()
        # And the commit should be gone from HEAD.
        cp = _git("log", "--oneline", cwd=fresh_repo)
        assert "seed commit" in cp.stdout
        assert "add new" not in cp.stdout

    def test_single_commit_refuses(self, fresh_repo: Path):
        # fresh_repo has exactly one commit
        result = reset_last_commit(fresh_repo)
        assert result.success is False
        assert result.error is not None
        assert "only one commit" in result.error

    def test_empty_repo_refuses(self, empty_vault: Path):
        _git("init", "-q", cwd=empty_vault)
        result = reset_last_commit(empty_vault)
        assert result.success is False

    def test_not_a_repo_fails(self, empty_vault: Path):
        result = reset_last_commit(empty_vault)
        assert result.success is False
        assert result.error is not None

    def test_nonexistent_vault_fails(self, tmp_path: Path):
        result = reset_last_commit(tmp_path / "nope")
        assert result.success is False


# ===========================================================================
# files_changed_in_last_commit
# ===========================================================================

class TestFilesChangedInLastCommit:
    def test_single_file_commit(self, fresh_repo: Path):
        files = files_changed_in_last_commit(fresh_repo)
        assert files == ["seed.md"]

    def test_two_files_modified(self, fresh_repo: Path):
        (fresh_repo / "a.md").write_text("a")
        (fresh_repo / "b.md").write_text("b")
        _git("add", "a.md", "b.md", cwd=fresh_repo)
        _git("commit", "-q", "-m", "add two files", cwd=fresh_repo)

        files = files_changed_in_last_commit(fresh_repo)
        assert set(files) == {"a.md", "b.md"}

    def test_new_file(self, fresh_repo: Path):
        (fresh_repo / "new.md").write_text("# new\n")
        _git("add", "new.md", cwd=fresh_repo)
        _git("commit", "-q", "-m", "add new", cwd=fresh_repo)

        files = files_changed_in_last_commit(fresh_repo)
        assert files == ["new.md"]

    def test_empty_repo_returns_empty_list(self, empty_vault: Path):
        _git("init", "-q", cwd=empty_vault)
        files = files_changed_in_last_commit(empty_vault)
        assert files == []

    def test_not_a_repo_returns_empty(self, empty_vault: Path):
        files = files_changed_in_last_commit(empty_vault)
        assert files == []

    def test_nonexistent_vault_returns_empty(self, tmp_path: Path):
        files = files_changed_in_last_commit(tmp_path / "nope")
        assert files == []


# ===========================================================================
# Default constants
# ===========================================================================

class TestConstants:
    def test_default_author_is_secondbrain_identity(self):
        assert "secondbrain" in DEFAULT_AUTHOR.lower()
        assert "@" in DEFAULT_AUTHOR

    def test_gitignore_has_obsidian_workspace(self):
        assert ".obsidian/workspace.json" in DEFAULT_GITIGNORE

    def test_gitignore_has_ds_store(self):
        assert ".DS_Store" in DEFAULT_GITIGNORE

    def test_gitignore_has_secondbrain_runtime(self):
        assert ".secondbrain/cursors/" in DEFAULT_GITIGNORE


# ===========================================================================
# CLI entry point (subprocess-level)
# ===========================================================================

def _run_cli(*args: str) -> subprocess.CompletedProcess:
    """Run vault_git.py as a subprocess, capturing stdout/stderr."""
    return subprocess.run(
        [sys.executable, str(VAULT_GIT_SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


class TestCLI:
    def test_init_subcommand_creates_git(self, empty_vault: Path):
        result = _run_cli("init", "--vault", str(empty_vault))
        assert result.returncode == 0, result.stderr
        assert (empty_vault / ".git").is_dir()

    def test_init_with_remote(self, empty_vault: Path, tmp_path: Path):
        bare = tmp_path / "bare.git"
        subprocess.run(
            ["git", "init", "--bare", "-q", str(bare)],
            capture_output=True,
            check=True,
        )
        remote_url = bare.as_uri()
        result = _run_cli("init", "--vault", str(empty_vault), "--remote", remote_url)
        assert result.returncode == 0, result.stderr
        cp = _git("remote", "get-url", "origin", cwd=empty_vault)
        assert cp.returncode == 0
        assert cp.stdout.strip() == remote_url

    def test_init_nonexistent_vault(self, tmp_path: Path):
        result = _run_cli("init", "--vault", str(tmp_path / "nope"))
        assert result.returncode != 0

    def test_status_subcommand(self, fresh_repo: Path):
        result = _run_cli("status", "--vault", str(fresh_repo))
        assert result.returncode == 0
        # Should print something sensible (stdout non-empty).
        assert result.stdout.strip()

    def test_status_not_a_repo(self, empty_vault: Path):
        result = _run_cli("status", "--vault", str(empty_vault))
        assert result.returncode != 0

    def test_commit_stop_clean_repo(self, fresh_repo: Path):
        result = _run_cli(
            "commit-stop", "--vault", str(fresh_repo), "--message", "test"
        )
        assert result.returncode == 0, result.stderr
        # No change → should be a no-op but succeed
        cp = _git("log", "-1", "--format=%s", cwd=fresh_repo)
        assert cp.stdout.strip() == "seed commit"

    def test_commit_stop_with_changes(self, fresh_repo: Path):
        (fresh_repo / "new.md").write_text("# new\n")
        result = _run_cli(
            "commit-stop", "--vault", str(fresh_repo), "--message", "cli commit"
        )
        assert result.returncode == 0, result.stderr
        cp = _git("log", "-1", "--format=%s", cwd=fresh_repo)
        assert cp.stdout.strip() == "cli commit"

    def test_commit_stop_default_message(self, fresh_repo: Path):
        (fresh_repo / "new.md").write_text("# new\n")
        result = _run_cli("commit-stop", "--vault", str(fresh_repo))
        assert result.returncode == 0
        cp = _git("log", "-1", "--format=%s", cwd=fresh_repo)
        assert "Session checkpoint" in cp.stdout

    def test_last_commit_files(self, fresh_repo: Path):
        result = _run_cli("last-commit-files", "--vault", str(fresh_repo))
        assert result.returncode == 0
        assert "seed.md" in result.stdout

    def test_reset_last_commit_single_commit_fails(self, fresh_repo: Path):
        result = _run_cli("reset-last-commit", "--vault", str(fresh_repo))
        assert result.returncode != 0

    def test_reset_last_commit_succeeds(self, fresh_repo: Path):
        (fresh_repo / "new.md").write_text("# new\n")
        _git("add", "new.md", cwd=fresh_repo)
        _git("commit", "-q", "-m", "add new", cwd=fresh_repo)

        result = _run_cli("reset-last-commit", "--vault", str(fresh_repo))
        assert result.returncode == 0, result.stderr
        assert not (fresh_repo / "new.md").exists()

    def test_unknown_subcommand_fails(self, fresh_repo: Path):
        result = _run_cli("nonsense", "--vault", str(fresh_repo))
        assert result.returncode != 0

    def test_missing_vault_arg_fails(self):
        result = _run_cli("init")
        assert result.returncode != 0
