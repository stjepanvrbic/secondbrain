#!/usr/bin/env python3
"""
vault_git.py — stdlib-only helper for git operations on the user's Obsidian vault.

This module is the foundation of Phase 2 (lifecycle redesign):

  - T7 (this file): git helper library + CLI entry point
  - T8: init flow asks for consent and calls init_repo/write_gitignore/initial_commit
  - T9: Stop hook calls `vault_git.py commit-stop` after every agent turn

CRITICAL: all functions operate on the VAULT, never on the secondbrain plugin
repo itself. The vault path is always supplied by the caller; this module has
no global default and never infers "the current repo".

Every public function returns a StepResult so callers get uniform success
reporting with the `did_work` bit that doctor and init use elsewhere. Git
command failures become `StepResult(success=False, error=stderr)` — we never
let subprocess raise past the public API.

No side effects at import time. No `print()` calls from library functions
(the CLI entry point at the bottom of the file does its own printing).

Python 3.8+, zero external dependencies beyond the git CLI.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from setup_steps import StepResult  # type: ignore[reportMissingImports]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Distinct authorship so the user can `git log --author=user` to find their
# manual commits and `git log --author=secondbrain` to find ours. The email
# is intentionally under `.local` so it never resolves — the string is a
# label, not a contact.
DEFAULT_AUTHOR = "Claude (secondbrain) <noreply@secondbrain.local>"

# The canonical `.gitignore` we write into a freshly-tracked vault. Intent:
# keep user-authored notes tracked while ignoring Obsidian's per-session UI
# state, OS clutter, and secondbrain's runtime bookkeeping (cursors and the
# ingest log). The ingest log is excluded because Phase 3's ingester will
# rewrite it constantly — versioning its churn would bury the vault's real
# history in noise.
DEFAULT_GITIGNORE = """\
# Obsidian
.obsidian/workspace.json
.obsidian/workspace-mobile.json
.obsidian/cache/
.obsidian/.trash/

# OS
.DS_Store
Thumbs.db

# Secondbrain — runtime state
.secondbrain/cursors/
.secondbrain/ingest-log.md
"""

# The canonical subject line for the very first commit on a secondbrain-managed
# vault. Stable so T8's init flow, doctor, and the undo-last-turn skill can
# all reason about it.
_INITIAL_COMMIT_MESSAGE = "Initial secondbrain vault scaffolding"

# Default message used by the Stop hook when no message is supplied on the
# command line. T9 may override this per-turn with a summary from the session.
_DEFAULT_STOP_MESSAGE = "Session checkpoint"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Commit:
    """A commit's summary fields as returned by `git log`.

    `date` is an ISO-8601 string (git's `%aI` format) — we keep it as a string
    rather than datetime so callers can round-trip it to JSON without any
    conversion. `message` is the subject line only (no body), which is what
    the undo-last-turn UI displays.
    """
    sha: str
    author: str
    date: str
    message: str


def _step_result(
    success: bool,
    message: str,
    did_work: bool,
    error: Optional[str] = None,
) -> "StepResult":
    """Return a StepResult, importing it lazily to avoid a hard dep on
    setup_steps at module load time.

    Lazy import is deliberate: setup_steps imports init_obsidian which pulls
    in a decent chunk of the plugin surface, and we want `vault_git` to be
    importable from a bare script runner without dragging all that along.
    """
    from setup_steps import StepResult as _StepResult  # type: ignore[reportMissingImports]  # noqa: PLC0415
    return _StepResult(
        success=success,
        message=message,
        did_work=did_work,
        error=error,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _git_available() -> bool:
    """Return True iff the `git` binary is on PATH.

    Uses `shutil.which` rather than running `git --version` so a single call
    is cheap enough to check on every public API entry without slowing hot
    paths like the Stop hook.
    """
    return shutil.which("git") is not None


def _run_git(
    vault_path: Path,
    args: List[str],
    *,
    env: Optional[dict[str, str]] = None,
    check: bool = False,
) -> subprocess.CompletedProcess:
    """Run a git command with `cwd=vault_path` and return the CompletedProcess.

    Never raises on non-zero exit unless `check=True`. Always captures both
    stdout and stderr. We pass the command as a list (never `shell=True`) so
    argument handling is predictable regardless of shell quoting rules.
    """
    return subprocess.run(
        ["git", *args],
        cwd=str(vault_path),
        env={**os.environ, **(env or {})},
        capture_output=True,
        text=True,
        check=check,
    )


_IDENT_RE = re.compile(r"^(?P<name>.+?)\s*<(?P<email>[^>]+)>$")


def _fallback_committer_env(vault_path: Path, author: str) -> dict[str, str]:
    """Return env overrides only when git cannot resolve a committer ident.

    Cowork's sandbox may export blank GIT_* identity variables and omit the
    user's global ~/.gitconfig. In that state `git commit --author ...` still
    fails because git resolves author and committer separately. We preserve the
    user's real committer identity whenever git can resolve one; otherwise we
    fall back to the same identity as `author` so automated commits stay
    non-interactive and deterministic.
    """
    ident = _run_git(vault_path, ["var", "GIT_COMMITTER_IDENT"])
    if ident.returncode == 0:
        return {}

    match = _IDENT_RE.match(author.strip())
    if not match:
        return {}

    return {
        "GIT_COMMITTER_NAME": match.group("name").strip(),
        "GIT_COMMITTER_EMAIL": match.group("email").strip(),
    }


def _ensure_vault_exists(vault_path: Path) -> Optional["StepResult"]:
    """Return None on success, or a failure StepResult the caller should return."""
    if not vault_path.exists():
        return _step_result(
            success=False,
            message="vault path does not exist",
            did_work=False,
            error=f"vault path does not exist: {vault_path}",
        )
    if not vault_path.is_dir():
        return _step_result(
            success=False,
            message="vault path is not a directory",
            did_work=False,
            error=f"vault path is not a directory: {vault_path}",
        )
    return None


def _ensure_git_available() -> Optional["StepResult"]:
    """Return None on success, or a failure StepResult the caller should return."""
    if not _git_available():
        return _step_result(
            success=False,
            message="git not installed",
            did_work=False,
            error="git binary not found on PATH",
        )
    return None


def _require_repo(vault_path: Path) -> Optional["StepResult"]:
    """Return None on success, or a failure StepResult the caller should return.

    Used by functions that mutate an existing repo (commit_changes,
    reset_last_commit, ...). Reports a uniform error when the caller hasn't
    yet run `vault_git.py init`.
    """
    if not is_git_repo(vault_path):
        return _step_result(
            success=False,
            message="not a git repo; run vault_git.py init first",
            did_work=False,
            error=f"not a git repository: {vault_path}",
        )
    return None


def _count_commits(vault_path: Path) -> int:
    """Return the number of commits reachable from HEAD. 0 if repo is empty.

    Used by reset_last_commit to tell a one-commit repo from a zero-commit
    repo so we can refuse both correctly.
    """
    cp = _run_git(vault_path, ["rev-list", "--count", "HEAD"])
    if cp.returncode != 0:
        return 0
    try:
        return int(cp.stdout.strip())
    except ValueError:
        return 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_git_repo(vault_path: Path) -> bool:
    """Return True iff `vault_path` is a directory backed by a git repo.

    Never raises, even on nonexistent paths or file arguments. The check
    runs `git rev-parse --is-inside-work-tree` so it handles both regular
    repos and linked worktrees — `.git` can be a file (for worktrees) rather
    than a directory. Bare repos are also recognized, though vaults should
    never be bare.
    """
    if not vault_path.exists() or not vault_path.is_dir():
        return False
    if not _git_available():
        return False
    cp = _run_git(vault_path, ["rev-parse", "--is-inside-work-tree"])
    return cp.returncode == 0 and cp.stdout.strip() == "true"


def init_repo(
    vault_path: Path,
    *,
    with_remote: bool = False,
    remote_url: Optional[str] = None,
    dry_run: bool = False,
) -> "StepResult":
    """Run `git init` in `vault_path` and optionally add a remote.

    Idempotent: if the path is already a git repo, `did_work` is False and no
    further work happens (even if `with_remote=True` — we don't clobber an
    existing remote configuration). The caller should use git directly for
    advanced remote management.

    Does NOT make an initial commit — that's `initial_commit()`'s job so the
    two steps stay separable for dry-run and doctor treatment flows.
    """
    err = _ensure_git_available()
    if err is not None:
        return err
    err = _ensure_vault_exists(vault_path)
    if err is not None:
        return err

    already_repo = is_git_repo(vault_path)

    if dry_run:
        if already_repo:
            return _step_result(
                success=True,
                message=f"init_repo: {vault_path} is already a git repo",
                did_work=False,
            )
        return _step_result(
            success=True,
            message=f"init_repo: would run `git init` in {vault_path}",
            did_work=False,
        )

    if already_repo:
        return _step_result(
            success=True,
            message=f"init_repo: {vault_path} is already a git repo",
            did_work=False,
        )

    cp = _run_git(vault_path, ["init", "-q"])
    if cp.returncode != 0:
        return _step_result(
            success=False,
            message="init_repo: git init failed",
            did_work=False,
            error=(cp.stderr or cp.stdout).strip() or "git init returned nonzero",
        )

    if with_remote and remote_url:
        # Check whether an `origin` remote already exists (shouldn't, since
        # we only get here after a fresh init, but be defensive).
        existing = _run_git(vault_path, ["remote", "get-url", "origin"])
        if existing.returncode == 0:
            # Already has an origin — leave it alone, don't clobber.
            return _step_result(
                success=True,
                message=(
                    f"init_repo: initialized {vault_path}; "
                    f"origin already set to {existing.stdout.strip()}"
                ),
                did_work=True,
            )
        add = _run_git(vault_path, ["remote", "add", "origin", remote_url])
        if add.returncode != 0:
            return _step_result(
                success=False,
                message="init_repo: git remote add failed",
                did_work=True,  # init itself did work; remote add is the failure
                error=(add.stderr or add.stdout).strip() or "git remote add failed",
            )
        return _step_result(
            success=True,
            message=f"init_repo: initialized {vault_path} with remote {remote_url}",
            did_work=True,
        )

    return _step_result(
        success=True,
        message=f"init_repo: initialized {vault_path}",
        did_work=True,
    )


def write_gitignore(vault_path: Path, dry_run: bool = False) -> "StepResult":
    """Write `DEFAULT_GITIGNORE` to `${vault_path}/.gitignore`.

    Idempotent: if the existing `.gitignore` already matches the default,
    `did_work` is False. If it differs (including user customizations), we
    replace it — this is a deliberate trade-off so doctor can always restore
    a known-good ignore list, and the user can re-apply any customizations
    on top afterward.
    """
    err = _ensure_vault_exists(vault_path)
    if err is not None:
        return err

    gitignore = vault_path / ".gitignore"
    if gitignore.exists():
        try:
            current = gitignore.read_text()
        except OSError as exc:
            return _step_result(
                success=False,
                message="write_gitignore: could not read existing .gitignore",
                did_work=False,
                error=str(exc),
            )
        if current == DEFAULT_GITIGNORE:
            return _step_result(
                success=True,
                message="write_gitignore: already up to date",
                did_work=False,
            )

    if dry_run:
        return _step_result(
            success=True,
            message=f"write_gitignore: would write {gitignore}",
            did_work=False,
        )

    try:
        gitignore.write_text(DEFAULT_GITIGNORE)
    except OSError as exc:
        return _step_result(
            success=False,
            message="write_gitignore: could not write .gitignore",
            did_work=False,
            error=str(exc),
        )

    return _step_result(
        success=True,
        message=f"write_gitignore: wrote {gitignore}",
        did_work=True,
    )


def initial_commit(
    vault_path: Path,
    *,
    author: str = DEFAULT_AUTHOR,
    dry_run: bool = False,
) -> "StepResult":
    """Make the first commit of the vault.

    Runs `git add -A` then `git commit` with the canonical secondbrain author
    and subject line. If the repo already has any commits, returns
    `did_work=False` — this function is the "cold start" step, not a general
    commit helper. Use `commit_changes()` for everything after.
    """
    err = _ensure_git_available()
    if err is not None:
        return err
    err = _ensure_vault_exists(vault_path)
    if err is not None:
        return err
    err = _require_repo(vault_path)
    if err is not None:
        return err

    if _count_commits(vault_path) > 0:
        return _step_result(
            success=True,
            message="initial_commit: repo already has commits",
            did_work=False,
        )

    if dry_run:
        return _step_result(
            success=True,
            message=f"initial_commit: would commit {vault_path}",
            did_work=False,
        )

    add = _run_git(vault_path, ["add", "-A"])
    if add.returncode != 0:
        return _step_result(
            success=False,
            message="initial_commit: git add -A failed",
            did_work=False,
            error=(add.stderr or add.stdout).strip() or "git add failed",
        )

    # Use `--author` so the commit is attributed to the secondbrain identity
    # regardless of the user's `user.name` / `user.email` git config. The
    # committer (who pushed the button) is still the real user — that's git's
    # standard author/committer split and we want to preserve it.
    commit = _run_git(
        vault_path,
        [
            "commit",
            "-q",
            "-m",
            _INITIAL_COMMIT_MESSAGE,
            "--author",
            author,
            "--allow-empty",
        ],
        env=_fallback_committer_env(vault_path, author),
    )
    if commit.returncode != 0:
        return _step_result(
            success=False,
            message="initial_commit: git commit failed",
            did_work=False,
            error=(commit.stderr or commit.stdout).strip() or "git commit failed",
        )

    return _step_result(
        success=True,
        message=f"initial_commit: committed {vault_path}",
        did_work=True,
    )


def has_uncommitted_changes(vault_path: Path) -> bool:
    """Return True iff `git status --porcelain` reports any output.

    Uses porcelain v1 (the default) since all we need is "anything pending?"
    — tracked modifications, staged changes, and untracked files all show up
    in the output. Never raises on bad paths; returns False instead so
    callers can treat "not a repo" as "nothing to commit".
    """
    if not vault_path.exists() or not vault_path.is_dir():
        return False
    if not _git_available():
        return False
    if not is_git_repo(vault_path):
        return False
    cp = _run_git(vault_path, ["status", "--porcelain"])
    if cp.returncode != 0:
        return False
    return bool(cp.stdout.strip())


def commit_changes(
    vault_path: Path,
    message: str,
    *,
    author: str = DEFAULT_AUTHOR,
    push: bool = False,
    dry_run: bool = False,
) -> "StepResult":
    """Stage all changes and commit with the given message.

    If there are no changes, returns `did_work=False` and `success=True` —
    a clean repo is a success, not a failure, for the Stop-hook use case.

    If `push=True`, after a successful commit we also `git push`. Push
    failures are reported in the result message but do NOT flip `success`
    to False: the commit itself is durable and we don't want the hook to
    leave the user's vault in a "broken" state just because a remote was
    unreachable. Doctor or the next turn can retry the push.
    """
    err = _ensure_git_available()
    if err is not None:
        return err
    err = _ensure_vault_exists(vault_path)
    if err is not None:
        return err
    err = _require_repo(vault_path)
    if err is not None:
        return err

    if not has_uncommitted_changes(vault_path):
        return _step_result(
            success=True,
            message="commit_changes: nothing to commit",
            did_work=False,
        )

    if dry_run:
        return _step_result(
            success=True,
            message=f"commit_changes: would commit {vault_path}",
            did_work=False,
        )

    add = _run_git(vault_path, ["add", "-A"])
    if add.returncode != 0:
        return _step_result(
            success=False,
            message="commit_changes: git add -A failed",
            did_work=False,
            error=(add.stderr or add.stdout).strip() or "git add failed",
        )

    commit = _run_git(
        vault_path,
        ["commit", "-q", "-m", message, "--author", author],
        env=_fallback_committer_env(vault_path, author),
    )
    if commit.returncode != 0:
        return _step_result(
            success=False,
            message="commit_changes: git commit failed",
            did_work=False,
            error=(commit.stderr or commit.stdout).strip() or "git commit failed",
        )

    if not push:
        return _step_result(
            success=True,
            message=f"commit_changes: committed {vault_path}",
            did_work=True,
        )

    # Push branch — the current branch, not a fixed name, so the user can
    # use whatever branch they prefer. Reporting failures in the message
    # keeps the StepResult contract simple (success stays True on push
    # failures; we prioritize the commit durability signal).
    push_cp = _run_git(vault_path, ["push"])
    if push_cp.returncode != 0:
        push_err = (push_cp.stderr or push_cp.stdout).strip() or "git push failed"
        return _step_result(
            success=True,  # commit succeeded; push is the soft failure
            message=f"commit_changes: committed but push failed: {push_err}",
            did_work=True,
        )

    return _step_result(
        success=True,
        message=f"commit_changes: committed and pushed {vault_path}",
        did_work=True,
    )


def list_recent_commits(vault_path: Path, n: int = 10) -> List[Commit]:
    """Return up to `n` most recent commits on the current branch.

    Uses a stable ASCII-unit-separator delimiter (U+001F) between fields so
    commit messages with arbitrary characters don't confuse the parser. Git
    guarantees `%H %an <%ae> %aI %s` are all safe to print; the separator
    just keeps them unambiguously apart.

    Returns an empty list on any failure (bad path, empty repo, git error) —
    this function must never raise, since it's called from UI-adjacent code
    like the undo-last-turn skill.
    """
    if not vault_path.exists() or not vault_path.is_dir():
        return []
    if not _git_available():
        return []
    if not is_git_repo(vault_path):
        return []

    if n <= 0:
        return []

    sep = "\x1f"
    fmt = sep.join(["%H", "%an <%ae>", "%aI", "%s"])
    cp = _run_git(
        vault_path,
        ["log", f"-{n}", f"--pretty=format:{fmt}"],
    )
    if cp.returncode != 0:
        # Empty repo ("does not have any commits yet") or any other error.
        return []

    commits: List[Commit] = []
    for line in cp.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split(sep)
        if len(parts) != 4:
            # Malformed line — skip rather than raise.
            continue
        sha, author, date, message = parts
        commits.append(Commit(sha=sha, author=author, date=date, message=message))
    return commits


def reset_last_commit(
    vault_path: Path,
    *,
    hard: bool = True,
) -> "StepResult":
    """Reset HEAD to HEAD~1 (hard by default).

    Used by the /secondbrain:undo-last-turn skill (T9). Fails if there's
    only one commit (we don't want to orphan the initial secondbrain
    scaffolding commit) or none at all. `hard=True` discards working-tree
    changes along with the commit; `hard=False` keeps the working tree so
    the user can re-commit after edits.
    """
    err = _ensure_git_available()
    if err is not None:
        return err
    err = _ensure_vault_exists(vault_path)
    if err is not None:
        return err
    err = _require_repo(vault_path)
    if err is not None:
        return err

    count = _count_commits(vault_path)
    if count == 0:
        return _step_result(
            success=False,
            message="reset_last_commit: no commits to reset",
            did_work=False,
            error="repo has no commits",
        )
    if count == 1:
        return _step_result(
            success=False,
            message="reset_last_commit: cannot reset, only one commit",
            did_work=False,
            error="cannot reset: only one commit exists (the initial scaffolding)",
        )

    mode = "--hard" if hard else "--mixed"
    cp = _run_git(vault_path, ["reset", mode, "HEAD~1"])
    if cp.returncode != 0:
        return _step_result(
            success=False,
            message="reset_last_commit: git reset failed",
            did_work=False,
            error=(cp.stderr or cp.stdout).strip() or "git reset failed",
        )

    return _step_result(
        success=True,
        message=f"reset_last_commit: rolled back to HEAD~1 ({mode})",
        did_work=True,
    )


def files_changed_in_last_commit(vault_path: Path) -> List[str]:
    """Return the list of files changed in HEAD (relative to vault root).

    Uses `git show --name-only --pretty=` which prints one file per line
    with no commit header noise. Returns an empty list if the repo has no
    commits or on any error — the caller (typically the undo-last-turn UI)
    treats an empty list as "nothing to undo".
    """
    if not vault_path.exists() or not vault_path.is_dir():
        return []
    if not _git_available():
        return []
    if not is_git_repo(vault_path):
        return []

    cp = _run_git(vault_path, ["show", "--name-only", "--pretty=", "HEAD"])
    if cp.returncode != 0:
        return []
    return [line.strip() for line in cp.stdout.splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# CLI entry point
#
# Subcommands are thin wrappers around the library functions. Each one
# parses args, calls the function, prints the resulting message, and
# exits 0 on success / non-zero on failure. The Stop hook (T9) shells out
# to `commit-stop`; the undo-last-turn skill (T9) shells out to
# `reset-last-commit`; init (T8) shells out to `init` + `status`.
# ---------------------------------------------------------------------------

def _cli_init(args) -> int:
    vault = Path(args.vault)
    result = init_repo(
        vault,
        with_remote=bool(args.remote),
        remote_url=args.remote,
    )
    sys.stdout.write(result.message + "\n")
    if result.error:
        sys.stderr.write(result.error + "\n")
    return 0 if result.success else 1


def _cli_commit_stop(args) -> int:
    vault = Path(args.vault)
    message = args.message or _DEFAULT_STOP_MESSAGE

    # Pre-flight: if the vault isn't a repo, print a clear message and exit
    # non-zero. This is the error path the Stop hook writes to ingest-log.md.
    if not vault.exists():
        sys.stderr.write(f"commit-stop: vault path does not exist: {vault}\n")
        return 1
    if not is_git_repo(vault):
        sys.stderr.write(
            "commit-stop: not a git repo; run `vault_git.py init` first\n"
        )
        return 1

    if not has_uncommitted_changes(vault):
        sys.stdout.write("commit-stop: nothing to commit\n")
        return 0

    result = commit_changes(
        vault,
        message,
        author=args.author or DEFAULT_AUTHOR,
        push=bool(args.push),
    )
    sys.stdout.write(result.message + "\n")
    if result.error:
        sys.stderr.write(result.error + "\n")
    return 0 if result.success else 1


def _cli_last_commit_files(args) -> int:
    vault = Path(args.vault)
    if not vault.exists():
        sys.stderr.write(f"last-commit-files: vault path does not exist: {vault}\n")
        return 1
    if not is_git_repo(vault):
        sys.stderr.write("last-commit-files: not a git repo\n")
        return 1

    files = files_changed_in_last_commit(vault)
    for f in files:
        sys.stdout.write(f + "\n")
    return 0


def _cli_reset_last_commit(args) -> int:
    vault = Path(args.vault)
    result = reset_last_commit(vault, hard=bool(args.hard))
    sys.stdout.write(result.message + "\n")
    if result.error:
        sys.stderr.write(result.error + "\n")
    return 0 if result.success else 1


def _cli_status(args) -> int:
    vault = Path(args.vault)
    if not vault.exists():
        sys.stderr.write(f"status: vault path does not exist: {vault}\n")
        return 1
    if not is_git_repo(vault):
        sys.stderr.write(f"status: not a git repo: {vault}\n")
        return 1

    # Print a summary the Stop hook / doctor can paste into a log file.
    count = _count_commits(vault)
    dirty = has_uncommitted_changes(vault)
    recent = list_recent_commits(vault, n=3)
    sys.stdout.write(f"vault: {vault}\n")
    sys.stdout.write(f"commits: {count}\n")
    sys.stdout.write(f"uncommitted: {'yes' if dirty else 'no'}\n")
    if recent:
        sys.stdout.write("recent:\n")
        for c in recent:
            short = c.sha[:7] if len(c.sha) >= 7 else c.sha
            sys.stdout.write(f"  {short} {c.date} — {c.message}\n")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vault_git.py",
        description="Git operations on the secondbrain vault.",
    )
    subs = parser.add_subparsers(dest="subcommand", required=True)

    p_init = subs.add_parser("init", help="Initialize git in the vault")
    p_init.add_argument("--vault", required=True)
    p_init.add_argument("--remote", default=None, help="Optional remote URL")
    p_init.set_defaults(func=_cli_init)

    p_commit = subs.add_parser(
        "commit-stop", help="Commit pending changes (Stop hook)"
    )
    p_commit.add_argument("--vault", required=True)
    p_commit.add_argument("--message", default=None)
    p_commit.add_argument("--author", default=None)
    p_commit.add_argument("--push", action="store_true")
    p_commit.set_defaults(func=_cli_commit_stop)

    p_files = subs.add_parser(
        "last-commit-files", help="List files changed in HEAD"
    )
    p_files.add_argument("--vault", required=True)
    p_files.set_defaults(func=_cli_last_commit_files)

    p_reset = subs.add_parser(
        "reset-last-commit", help="Reset HEAD to HEAD~1"
    )
    p_reset.add_argument("--vault", required=True)
    p_reset.add_argument("--hard", action="store_true", default=True)
    p_reset.add_argument("--no-hard", dest="hard", action="store_false")
    p_reset.set_defaults(func=_cli_reset_last_commit)

    p_status = subs.add_parser("status", help="Show vault git status")
    p_status.add_argument("--vault", required=True)
    p_status.set_defaults(func=_cli_status)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
