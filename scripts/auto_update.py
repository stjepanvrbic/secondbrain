#!/usr/bin/env python3
"""
auto_update.py — Pull latest plugin version from marketplace repo.

Workaround for Claude Code/Cowork not auto-pulling the marketplace
git repo on plugin update. Runs quickly at session-start.

Usage:
    python3 auto_update.py              # pull and report
    python3 auto_update.py --check      # just check, don't pull
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Optional

MARKETPLACE_DIRS = [
    Path.home() / ".claude" / "plugins" / "marketplaces" / "secondbrain",
    Path.home() / ".claude" / "plugins" / "marketplaces" / "stjepanvrbic-secondbrain",
]


def find_marketplace() -> Optional[Path]:
    for d in MARKETPLACE_DIRS:
        if (d / ".git").is_dir():
            return d
    return None


def get_local_hash(repo: Path) -> Optional[str]:
    try:
        r = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def get_remote_hash(repo: Path) -> Optional[str]:
    try:
        r = subprocess.run(
            ["git", "-C", str(repo), "ls-remote", "origin", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip().split()[0]
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def pull(repo: Path) -> bool:
    try:
        r = subprocess.run(
            ["git", "-C", str(repo), "pull", "--ff-only", "origin", "main"],
            capture_output=True, text=True, timeout=15,
        )
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    check_only = "--check" in args

    repo = find_marketplace()
    if not repo:
        return 0  # not installed via marketplace, nothing to do

    local = get_local_hash(repo)
    remote = get_remote_hash(repo)

    if not local or not remote:
        return 0  # can't check, fail silently

    if local == remote:
        return 0  # up to date

    if check_only:
        print(f"Update available: {local[:7]} -> {remote[:7]}")
        return 1

    if pull(repo):
        new_hash = get_local_hash(repo)
        print(f"secondbrain updated: {local[:7]} -> {new_hash[:7] if new_hash else '?'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
