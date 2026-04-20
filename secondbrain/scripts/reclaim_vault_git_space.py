#!/usr/bin/env python3
"""
reclaim_vault_git_space.py — remove a legacy `<vault>/.git` directory.

Vault git versioning was removed in v3.6. External backup (Syncthing +
Google Drive) owns durability and rollback now. Pre-v3.6 installs may still
have a `<vault>/.git` that the plugin committed to on every Stop hook. That
directory can grow into the hundreds of GB and is the source of the slow
vault tooling users reported before the removal.

This script is the sanctioned, user-invoked way to reclaim that space.

- `--vault <path>` is required. Without `--confirm`, the script reports the
  size of `<vault>/.git` and exits without touching anything (a dry-run by
  default). With `--confirm`, it removes the directory.
- The script NEVER touches user data — only `<vault>/.git`. Your markdown
  files, frontmatter, and everything outside `.git` are left alone.
- Safe to run repeatedly. If `.git` is already gone, the script exits 0
  with a short message.

Example — read-only report:
    python3 reclaim_vault_git_space.py --vault ~/cowork

Example — actually reclaim the space:
    python3 reclaim_vault_git_space.py --vault ~/cowork --confirm
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def _dir_size_bytes(path: Path) -> int:
    total = 0
    for child in path.rglob("*"):
        try:
            if child.is_file():
                total += child.stat().st_size
        except OSError:
            continue
    return total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--vault",
        required=True,
        type=Path,
        help="Path to the vault root. Only `<vault>/.git` is affected.",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help=(
            "Actually delete `<vault>/.git`. Without this flag, the script "
            "is read-only and reports the size."
        ),
    )
    args = parser.parse_args(argv)

    vault: Path = args.vault.expanduser().resolve()
    if not vault.is_dir():
        print(f"error: vault not found: {vault}", file=sys.stderr)
        return 1

    git_dir = vault / ".git"
    if not git_dir.exists():
        print(f"reclaim_vault_git_space: no .git directory at {git_dir}; nothing to do")
        return 0

    if not git_dir.is_dir():
        # Linked worktrees use `.git` as a file pointing at the real git dir.
        # We refuse to touch that — the shared repo lives elsewhere, and
        # removing the pointer would silently break the user's worktree setup
        # without freeing the space they wanted to reclaim.
        print(
            f"error: {git_dir} is not a directory (it's a {git_dir.stat().st_mode:o} "
            "— likely a worktree pointer file). This script only removes a "
            "self-contained .git directory.",
            file=sys.stderr,
        )
        return 1

    size = _dir_size_bytes(git_dir)
    gb = size / (1024 ** 3)
    mb = size / (1024 ** 2)
    size_str = f"{gb:.2f} GB" if size >= 1024 ** 3 else f"{mb:.1f} MB"

    if not args.confirm:
        print(
            f"reclaim_vault_git_space: {git_dir} = {size_str}\n"
            f"  Re-run with --confirm to remove it and reclaim the space.\n"
            f"  External backup (Syncthing / Drive) already protects your "
            f"vault content; the plugin no longer commits to .git."
        )
        return 0

    try:
        shutil.rmtree(git_dir)
    except OSError as exc:
        print(f"error: failed to remove {git_dir}: {exc}", file=sys.stderr)
        return 1

    print(
        f"reclaim_vault_git_space: removed {git_dir} ({size_str} freed).\n"
        f"  Your vault files are untouched; only git history is gone. "
        f"Rollback via Syncthing / Drive version history going forward."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
