#!/usr/bin/env python3
"""
install_git_hooks.py — point git at the tracked hooks in .githooks/.

The tracked pre-push hook lives at <repo>/.githooks/pre-push. Git doesn't read
from that directory by default (it reads .git/hooks/), so we set
`core.hooksPath` to tell git to use the tracked directory instead.

This approach is better than symlinking or copying into .git/hooks/ because:
  - it's per-clone (not global)
  - it survives `git clone` for any contributor who runs this script
  - it leaves .git/hooks/ untouched (no conflict with IDE tooling)
  - it's version-controlled, so we can evolve the hook and everyone picks up
    the new version on pull

Usage:
    python3 secondbrain/scripts/install_git_hooks.py            # install
    python3 secondbrain/scripts/install_git_hooks.py --check    # verify only
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = REPO_ROOT / ".githooks"
EXPECTED_HOOKS = ["pre-push"]


def git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def current_hooks_path() -> str | None:
    try:
        return git("config", "--get", "core.hooksPath")
    except subprocess.CalledProcessError:
        return None


def follow_tags_enabled() -> bool:
    try:
        return git("config", "--get", "push.followTags") == "true"
    except subprocess.CalledProcessError:
        return False


def check() -> int:
    """Exit 0 if hooksPath is set correctly AND every expected hook exists + is executable."""
    current = current_hooks_path()
    expected = str(HOOKS_DIR.relative_to(REPO_ROOT))
    problems: list[str] = []

    if current != expected:
        problems.append(
            f"core.hooksPath is {current!r}, expected {expected!r}"
        )

    if not follow_tags_enabled():
        problems.append(
            "push.followTags is not 'true' — annotated tags won't be pushed automatically"
        )

    for hook in EXPECTED_HOOKS:
        p = HOOKS_DIR / hook
        if not p.is_file():
            problems.append(f"missing hook: {p}")
            continue
        if not (p.stat().st_mode & 0o111):
            problems.append(f"hook not executable: {p}")

    if problems:
        print("git hooks NOT correctly installed:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print("\nRun: python3 secondbrain/scripts/install_git_hooks.py", file=sys.stderr)
        return 1

    print(f"git hooks OK: core.hooksPath = {current}")
    return 0


def install() -> int:
    if not HOOKS_DIR.is_dir():
        print(f"error: {HOOKS_DIR} does not exist", file=sys.stderr)
        return 1

    # Set core.hooksPath relative to repo root so it stays valid across machines
    # and worktrees.
    relative = str(HOOKS_DIR.relative_to(REPO_ROOT))
    git("config", "core.hooksPath", relative)

    # Ensure `git push` carries annotated tags automatically. The normal
    # release path tags in GitHub Actions, but contributors still need
    # follow-tags for any explicit maintenance tags they create locally.
    git("config", "push.followTags", "true")

    # Make sure every expected hook is executable (matters after a fresh clone
    # on systems where +x wasn't preserved).
    for hook in EXPECTED_HOOKS:
        p = HOOKS_DIR / hook
        if p.is_file():
            mode = p.stat().st_mode
            if not (mode & 0o111):
                p.chmod(mode | 0o755)

    print(f"installed: git core.hooksPath = {relative}")
    return check()


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if "--check" in args:
        return check()
    return install()


if __name__ == "__main__":
    sys.exit(main())
