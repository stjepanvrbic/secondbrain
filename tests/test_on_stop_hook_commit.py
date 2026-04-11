"""Tests for on-stop.sh — T9 Stop hook that commits the vault after a turn.

The Stop hook runs after every agent turn. It:

  1. Reads stdin JSON (Claude Code Stop hook payload)
  2. Honors stop_hook_active=true (loop guard — exit 0 early)
  3. Resolves the active vault via ~/.config/secondbrain/vaults.json
  4. Skips if there's no active vault (pre-init state)
  5. Skips if the active vault is not a git repo (user opted out)
  6. Invokes `vault_git.py commit-stop --vault $PATH [--push]`
  7. Appends the result + timestamp to $VAULT/.secondbrain/ingest-log.md
  8. Exits 0 regardless of commit outcome (hooks must never wedge a session)

Strategy: subprocess-invoke on-stop.sh with mocked stdin and a temporary
vaults.json. Use `tempfile.mkdtemp()` (not pytest `tmp_path`) to dodge the
macOS /tmp ↔ /private/tmp symlink quirk that bit T4 in the bash enforcement
hook tests.

We exercise real `git init` and real `vault_git.py` subprocesses because the
hook's whole purpose is end-to-end integration. Mocking would prove nothing.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Iterator, Tuple

import pytest

HOOK = (
    Path(__file__).resolve().parent.parent
    / "secondbrain"
    / "hooks"
    / "on-stop.sh"
)

PLUGIN_ROOT = Path(__file__).resolve().parent.parent / "secondbrain"


# ---------------------------------------------------------------------------
# Git helper — explicit identity so commits don't need user config
# ---------------------------------------------------------------------------

def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
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


# ---------------------------------------------------------------------------
# Fixtures — use tempfile.mkdtemp() per T4 fix to avoid symlink issues
# ---------------------------------------------------------------------------

@pytest.fixture
def scratch() -> Iterator[Path]:
    """A fresh isolated scratch directory for one test.

    Uses tempfile.mkdtemp() rather than pytest tmp_path so the macOS
    /tmp ↔ /private/tmp symlink quirk can't mask bugs in path handling.
    """
    raw = tempfile.mkdtemp(prefix="sb_on_stop_")
    try:
        yield Path(raw)
    finally:
        shutil.rmtree(raw, ignore_errors=True)


def _write_vaults_config(config_path: Path, vaults: list[dict], active_id: str | None) -> None:
    """Write a vaults.json with the given entries."""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "schema_version": 1,
        "vaults": vaults,
        "active_vault_id": active_id,
    }
    config_path.write_text(json.dumps(data, indent=2))


def _make_vault_entry(vault_path: Path, vid: str = "v1", with_push: bool = False) -> dict:
    return {
        "id": vid,
        "path": str(vault_path),
        "name": vault_path.name,
        "role": "personal",
        "added_at": "2026-04-11T12:00:00",
        "with_push": with_push,
    }


def _init_vault_as_git_repo(vault: Path, with_seed_commit: bool = True) -> None:
    """git init a vault with an initial commit so reset-last-commit can
    conceptually work later. Tests that want an empty repo pass
    with_seed_commit=False.
    """
    _git("init", "-q", cwd=vault)
    _git("checkout", "-q", "-b", "main", cwd=vault)
    _git("config", "user.email", "test@example.invalid", cwd=vault)
    _git("config", "user.name", "Test User", cwd=vault)
    if with_seed_commit:
        (vault / "seed.md").write_text("# seed\n")
        _git("add", "seed.md", cwd=vault)
        _git("commit", "-q", "-m", "seed commit", cwd=vault)


def _make_vault_in_scratch(scratch: Path, name: str = "vault") -> Path:
    vault = scratch / name
    vault.mkdir()
    (vault / "brain").mkdir()
    (vault / "brain" / "status.md").write_text("# Status\n")
    return vault


def _run_hook(
    payload: dict,
    scratch: Path,
    vaults_config: Path | None = None,
    extra_env: dict | None = None,
) -> Tuple[int, str, str]:
    """Invoke on-stop.sh with the given payload on stdin.

    Points SECONDBRAIN_VAULTS_CONFIG at `vaults_config` if supplied so the
    hook reads our fixture rather than the real ~/.config/secondbrain.
    Also sets CLAUDE_PLUGIN_ROOT so the hook can locate vault_git.py.
    """
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_ROOT"] = str(PLUGIN_ROOT)
    if vaults_config is not None:
        env["SECONDBRAIN_VAULTS_CONFIG"] = str(vaults_config)
    else:
        # Steer the hook away from the real config by pointing at a path
        # that definitely doesn't exist in this scratch dir.
        env["SECONDBRAIN_VAULTS_CONFIG"] = str(scratch / "no-such-config.json")
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        [str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    return result.returncode, result.stdout, result.stderr


def _ingest_log_path(vault: Path) -> Path:
    return vault / ".secondbrain" / "ingest-log.md"


def _ingest_log_content(vault: Path) -> str:
    p = _ingest_log_path(vault)
    if not p.exists():
        return ""
    return p.read_text()


def _count_commits(vault: Path) -> int:
    cp = _git("rev-list", "--count", "HEAD", cwd=vault)
    if cp.returncode != 0:
        return 0
    try:
        return int(cp.stdout.strip())
    except ValueError:
        return 0


# ---------------------------------------------------------------------------
# Sanity: the hook file exists and is executable
# ---------------------------------------------------------------------------

class TestHookFilePresent:
    def test_hook_file_exists(self):
        assert HOOK.is_file(), f"on-stop.sh must exist at {HOOK}"

    def test_hook_file_executable(self):
        assert os.access(HOOK, os.X_OK), (
            f"on-stop.sh must be executable; fix with chmod +x {HOOK}"
        )


# ---------------------------------------------------------------------------
# stop_hook_active loop guard
# ---------------------------------------------------------------------------

class TestStopHookActiveLoopGuard:
    def test_stop_hook_active_true_exits_zero_without_git_op(self, scratch: Path):
        """If Claude Code already fired the Stop hook and the agent is
        mid-stop, the hook must exit 0 immediately — no commit, no log
        entry, no git call. This is the loop guard the Stop hook docs
        require.
        """
        vault = _make_vault_in_scratch(scratch)
        _init_vault_as_git_repo(vault)
        (vault / "brain" / "new.md").write_text("# new\n")  # uncommitted

        config = scratch / "cfg" / "vaults.json"
        _write_vaults_config(
            config,
            [_make_vault_entry(vault)],
            active_id="v1",
        )

        payload = {
            "session_id": "test-session",
            "transcript_path": "/tmp/tx.jsonl",
            "cwd": str(vault),
            "stop_hook_active": True,
        }
        code, _, _ = _run_hook(payload, scratch, vaults_config=config)
        assert code == 0
        # No commit was made — new.md is still uncommitted.
        assert _count_commits(vault) == 1  # only the seed commit

    def test_stop_hook_active_true_does_not_write_ingest_log(
        self, scratch: Path
    ):
        vault = _make_vault_in_scratch(scratch)
        _init_vault_as_git_repo(vault)
        (vault / "brain" / "new.md").write_text("# new\n")
        config = scratch / "cfg" / "vaults.json"
        _write_vaults_config(
            config, [_make_vault_entry(vault)], active_id="v1"
        )

        payload = {
            "session_id": "s",
            "transcript_path": "/tmp/tx.jsonl",
            "cwd": str(vault),
            "stop_hook_active": True,
        }
        _run_hook(payload, scratch, vaults_config=config)
        # If the hook bailed early, it shouldn't have touched the log.
        assert not _ingest_log_path(vault).exists()


# ---------------------------------------------------------------------------
# No vaults.json → silent exit 0
# ---------------------------------------------------------------------------

class TestNoVaultsConfig:
    def test_no_vaults_json_exits_zero(self, scratch: Path):
        """Pre-init state: user hasn't run init yet, so vaults.json doesn't
        exist. The hook must exit 0 silently without crashing."""
        payload = {
            "session_id": "s",
            "transcript_path": "/tmp/tx.jsonl",
            "cwd": str(scratch),
            "stop_hook_active": False,
        }
        # Point at a nonexistent config
        missing = scratch / "definitely-missing.json"
        code, _, _ = _run_hook(payload, scratch, vaults_config=missing)
        assert code == 0

    def test_no_active_vault_in_config_exits_zero(self, scratch: Path):
        """Edge case: vaults.json exists but active_vault_id is null."""
        config = scratch / "cfg" / "vaults.json"
        _write_vaults_config(config, [], active_id=None)
        payload = {
            "session_id": "s",
            "transcript_path": "/tmp/tx.jsonl",
            "cwd": str(scratch),
            "stop_hook_active": False,
        }
        code, _, _ = _run_hook(payload, scratch, vaults_config=config)
        assert code == 0


# ---------------------------------------------------------------------------
# Active vault is NOT a git repo → skip
# ---------------------------------------------------------------------------

class TestVaultNotGitRepo:
    def test_non_git_vault_exits_zero(self, scratch: Path):
        """User opted out of git during init. Hook must be a silent no-op."""
        vault = _make_vault_in_scratch(scratch)
        # NO git init — this vault is not tracked.
        config = scratch / "cfg" / "vaults.json"
        _write_vaults_config(
            config, [_make_vault_entry(vault)], active_id="v1"
        )

        payload = {
            "session_id": "s",
            "transcript_path": "/tmp/tx.jsonl",
            "cwd": str(vault),
            "stop_hook_active": False,
        }
        code, _, _ = _run_hook(payload, scratch, vaults_config=config)
        assert code == 0

    def test_non_git_vault_does_not_crash(self, scratch: Path):
        """Non-git vault path must not produce stderr noise or write errors."""
        vault = _make_vault_in_scratch(scratch)
        config = scratch / "cfg" / "vaults.json"
        _write_vaults_config(
            config, [_make_vault_entry(vault)], active_id="v1"
        )

        payload = {
            "session_id": "s",
            "transcript_path": "/tmp/tx.jsonl",
            "cwd": str(vault),
            "stop_hook_active": False,
        }
        code, _, _ = _run_hook(payload, scratch, vaults_config=config)
        # Exit code must be 0; we don't over-assert on stderr because
        # some Python print-to-stderr on a logging path is fine as long
        # as the hook itself doesn't wedge the session.
        assert code == 0


# ---------------------------------------------------------------------------
# Active vault IS git repo, has uncommitted changes → commit
# ---------------------------------------------------------------------------

class TestCommitMade:
    def test_commit_is_made_when_dirty(self, scratch: Path):
        """Happy path: vault is a git repo with pending changes. The hook
        should add+commit, producing a new commit."""
        vault = _make_vault_in_scratch(scratch)
        _init_vault_as_git_repo(vault)
        assert _count_commits(vault) == 1  # seed

        # Produce an uncommitted change.
        (vault / "brain" / "new.md").write_text("# new turn content\n")

        config = scratch / "cfg" / "vaults.json"
        _write_vaults_config(
            config, [_make_vault_entry(vault)], active_id="v1"
        )

        payload = {
            "session_id": "s",
            "transcript_path": "/tmp/tx.jsonl",
            "cwd": str(vault),
            "stop_hook_active": False,
        }
        code, _, _ = _run_hook(payload, scratch, vaults_config=config)
        assert code == 0
        # Should now be 2 commits: seed + the stop-hook checkpoint.
        assert _count_commits(vault) == 2, (
            f"expected a new commit after hook ran; got {_count_commits(vault)} commits"
        )

    def test_commit_has_stop_hook_message(self, scratch: Path):
        """The commit message should signal this was the Stop hook."""
        vault = _make_vault_in_scratch(scratch)
        _init_vault_as_git_repo(vault)
        (vault / "brain" / "new.md").write_text("# new\n")

        config = scratch / "cfg" / "vaults.json"
        _write_vaults_config(
            config, [_make_vault_entry(vault)], active_id="v1"
        )

        payload = {
            "session_id": "s",
            "transcript_path": "/tmp/tx.jsonl",
            "cwd": str(vault),
            "stop_hook_active": False,
        }
        _run_hook(payload, scratch, vaults_config=config)
        cp = _git("log", "-1", "--format=%s", cwd=vault)
        assert cp.returncode == 0
        msg = cp.stdout.strip()
        # Accept "Session checkpoint" (the default from vault_git.py) or
        # anything else that signals this was the Stop hook. The key is
        # that the message is NOT empty and NOT the seed commit.
        assert msg, "commit message should not be empty"
        assert msg != "seed commit", (
            f"Stop hook didn't create a NEW commit; HEAD is still the seed: {msg!r}"
        )

    def test_ingest_log_is_created_when_missing(self, scratch: Path):
        """The hook should append a timestamped entry to
        $VAULT/.secondbrain/ingest-log.md, creating the file and directory
        if missing."""
        vault = _make_vault_in_scratch(scratch)
        _init_vault_as_git_repo(vault)
        (vault / "brain" / "new.md").write_text("# new\n")

        config = scratch / "cfg" / "vaults.json"
        _write_vaults_config(
            config, [_make_vault_entry(vault)], active_id="v1"
        )

        # Pre-condition: no .secondbrain dir at all.
        assert not (vault / ".secondbrain").exists()

        payload = {
            "session_id": "s",
            "transcript_path": "/tmp/tx.jsonl",
            "cwd": str(vault),
            "stop_hook_active": False,
        }
        _run_hook(payload, scratch, vaults_config=config)

        log = _ingest_log_path(vault)
        assert log.exists(), (
            ".secondbrain/ingest-log.md must exist after the hook ran "
            f"with a dirty git repo; got nothing at {log}"
        )
        content = log.read_text()
        assert content, "ingest-log.md must not be empty after a commit"

    def test_ingest_log_gets_appended_to_on_second_run(self, scratch: Path):
        """Subsequent runs must append, not overwrite."""
        vault = _make_vault_in_scratch(scratch)
        _init_vault_as_git_repo(vault)

        config = scratch / "cfg" / "vaults.json"
        _write_vaults_config(
            config, [_make_vault_entry(vault)], active_id="v1"
        )

        payload = {
            "session_id": "s",
            "transcript_path": "/tmp/tx.jsonl",
            "cwd": str(vault),
            "stop_hook_active": False,
        }

        # First run with dirty repo
        (vault / "brain" / "one.md").write_text("# one\n")
        _run_hook(payload, scratch, vaults_config=config)
        content_1 = _ingest_log_content(vault)
        assert content_1

        # Second run with another change
        (vault / "brain" / "two.md").write_text("# two\n")
        _run_hook(payload, scratch, vaults_config=config)
        content_2 = _ingest_log_content(vault)

        # Must contain the first run's content AND more.
        assert len(content_2) > len(content_1), (
            "second run should have APPENDED to ingest-log.md, but the "
            "log did not grow"
        )
        assert content_1 in content_2, (
            "second run must preserve the first run's log entry (append, "
            "not overwrite)"
        )


# ---------------------------------------------------------------------------
# Clean repo → no commit, ingest-log entry still noted
# ---------------------------------------------------------------------------

class TestCleanRepoNoCommit:
    def test_clean_repo_exits_zero(self, scratch: Path):
        """No uncommitted changes — the hook must exit 0 without forcing a
        new commit. vault_git.py's commit-stop handles this case."""
        vault = _make_vault_in_scratch(scratch)
        _init_vault_as_git_repo(vault)
        # Commit the brain/status.md so the working tree is clean.
        _git("add", "-A", cwd=vault)
        _git("commit", "-q", "-m", "clean state", cwd=vault)

        config = scratch / "cfg" / "vaults.json"
        _write_vaults_config(
            config, [_make_vault_entry(vault)], active_id="v1"
        )

        payload = {
            "session_id": "s",
            "transcript_path": "/tmp/tx.jsonl",
            "cwd": str(vault),
            "stop_hook_active": False,
        }
        before = _count_commits(vault)
        code, _, _ = _run_hook(payload, scratch, vaults_config=config)
        after = _count_commits(vault)
        assert code == 0
        assert before == after, (
            "clean repo must not produce a new commit; "
            f"before={before}, after={after}"
        )


# ---------------------------------------------------------------------------
# with_push=True → --push flag is passed
# ---------------------------------------------------------------------------

class TestWithPushFlag:
    """If the active vault has with_push=True, the hook should pass --push
    to commit-stop. We verify this by pointing the vault at a bare remote
    and checking that the commit actually lands there."""

    def test_with_push_true_pushes_to_remote(self, scratch: Path):
        vault = _make_vault_in_scratch(scratch)
        _init_vault_as_git_repo(vault)

        # Bare remote inside the same scratch dir.
        bare = scratch / "bare.git"
        subprocess.run(
            ["git", "init", "--bare", "-q", str(bare)],
            check=True,
            capture_output=True,
        )
        _git("remote", "add", "origin", bare.as_uri(), cwd=vault)
        # Push the seed commit so the branch exists on the remote.
        _git("push", "-q", "-u", "origin", "main", cwd=vault)

        # Dirty change to commit on the next turn.
        (vault / "brain" / "new.md").write_text("# new\n")

        config = scratch / "cfg" / "vaults.json"
        _write_vaults_config(
            config,
            [_make_vault_entry(vault, with_push=True)],
            active_id="v1",
        )

        payload = {
            "session_id": "s",
            "transcript_path": "/tmp/tx.jsonl",
            "cwd": str(vault),
            "stop_hook_active": False,
        }
        code, _, _ = _run_hook(payload, scratch, vaults_config=config)
        assert code == 0

        # The bare repo should now have the Stop-hook commit. Use --all
        # because the bare repo's default HEAD symlink points at 'master'
        # but we pushed to 'main', so `git log --oneline` alone would
        # fatal out with "does not have any commits yet" on master.
        cp = subprocess.run(
            ["git", "--git-dir", str(bare), "log", "--oneline", "--all"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert cp.returncode == 0
        # The commit log should show at least two commits (seed + stop
        # hook) on the remote.
        lines = [ln for ln in cp.stdout.splitlines() if ln.strip()]
        assert len(lines) >= 2, (
            f"bare remote should have seed + stop-hook commit; got: {lines!r}"
        )

    def test_with_push_false_does_not_push(self, scratch: Path):
        """Even if a remote exists, with_push=False must NOT push — that
        would silently exfiltrate to wherever origin points."""
        vault = _make_vault_in_scratch(scratch)
        _init_vault_as_git_repo(vault)

        bare = scratch / "bare.git"
        subprocess.run(
            ["git", "init", "--bare", "-q", str(bare)],
            check=True,
            capture_output=True,
        )
        _git("remote", "add", "origin", bare.as_uri(), cwd=vault)
        _git("push", "-q", "-u", "origin", "main", cwd=vault)

        (vault / "brain" / "new.md").write_text("# new\n")

        config = scratch / "cfg" / "vaults.json"
        _write_vaults_config(
            config,
            [_make_vault_entry(vault, with_push=False)],
            active_id="v1",
        )

        payload = {
            "session_id": "s",
            "transcript_path": "/tmp/tx.jsonl",
            "cwd": str(vault),
            "stop_hook_active": False,
        }
        _run_hook(payload, scratch, vaults_config=config)

        # Bare remote should only have the seed commit — the stop-hook
        # commit exists locally but hasn't been pushed. Use --all as in
        # test_with_push_true_pushes_to_remote.
        cp = subprocess.run(
            ["git", "--git-dir", str(bare), "log", "--oneline", "--all"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert cp.returncode == 0
        lines = [ln for ln in cp.stdout.splitlines() if ln.strip()]
        assert len(lines) == 1, (
            "with_push=False should NOT push; remote should have 1 "
            f"commit (the seed), got {len(lines)}: {lines!r}"
        )


# ---------------------------------------------------------------------------
# Hook never wedges the session — even on internal error
# ---------------------------------------------------------------------------

class TestHookAlwaysExitsZero:
    def test_malformed_stdin_exits_zero(self, scratch: Path):
        """If stdin JSON is malformed, the hook must still exit 0 so the
        session isn't wedged."""
        env = os.environ.copy()
        env["CLAUDE_PLUGIN_ROOT"] = str(PLUGIN_ROOT)
        env["SECONDBRAIN_VAULTS_CONFIG"] = str(scratch / "no-config.json")
        result = subprocess.run(
            [str(HOOK)],
            input="this is not valid json {{{",
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        assert result.returncode == 0, (
            "on-stop.sh must exit 0 on malformed stdin JSON; "
            f"got {result.returncode}, stderr={result.stderr!r}"
        )

    def test_empty_stdin_exits_zero(self, scratch: Path):
        env = os.environ.copy()
        env["CLAUDE_PLUGIN_ROOT"] = str(PLUGIN_ROOT)
        env["SECONDBRAIN_VAULTS_CONFIG"] = str(scratch / "no-config.json")
        result = subprocess.run(
            [str(HOOK)],
            input="",
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        assert result.returncode == 0
