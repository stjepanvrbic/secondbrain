#!/usr/bin/env python3
"""
migrate_v2_to_v3.py — Migrate a v2.x secondbrain vault to v3 structure.

The approach: move any deprecated files into inbox/ for re-ingestion.
The ingest skill (via dream-protocol) will route their content to the
right places using current routing rules. Nothing is lost, nothing is
hardcoded to a specific destination.

Deprecated items (as of v3):
- brain/commitments.md — tasks now live in brain/status.md
- Any other files that should be re-ingested

Usage:
    python3 migrate_v2_to_v3.py /path/to/vault
    python3 migrate_v2_to_v3.py /path/to/vault --dry-run
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

# Files that were part of v2 structure but are no longer maintained in v3.
# These get moved to inbox/ for re-ingestion by the ingest skill.
DEPRECATED_FILES = [
    "brain/commitments.md",
]


def move_to_inbox(vault: Path, rel_path: str, dry_run: bool = False) -> bool:
    """Move a deprecated file into inbox/ with a clear prefix. Returns True if moved."""
    src = vault / rel_path
    if not src.exists():
        return False

    inbox = vault / "inbox"
    # Use a prefix so it's obviously a migration artifact and doesn't collide
    # with user-created inbox items.
    dest_name = f"migration--{rel_path.replace('/', '--')}"
    dest = inbox / dest_name

    # If dest exists, add timestamp
    if dest.exists():
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        stem = dest.stem
        dest = inbox / f"{stem}-{ts}.md"

    if dry_run:
        print(f"  Would move: {rel_path} -> inbox/{dest.name}")
        return True

    inbox.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    print(f"  Moved: {rel_path} -> inbox/{dest.name}")
    return True


def migrate(vault: Path, dry_run: bool = False) -> int:
    """Move all deprecated files into inbox/. Returns count of moved files."""
    moved = 0
    for rel in DEPRECATED_FILES:
        if move_to_inbox(vault, rel, dry_run):
            moved += 1
    return moved


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate v2 secondbrain vault to v3 structure")
    parser.add_argument("vault", type=Path, help="Path to the Obsidian vault root")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without applying")
    args = parser.parse_args(argv)

    vault = args.vault.expanduser().resolve()
    if not vault.is_dir():
        print(f"Error: vault not found: {vault}", file=sys.stderr)
        return 1

    print(f"Migrating {vault} to v3 structure" + (" (dry run)" if args.dry_run else ""))
    print("")
    print("Moving deprecated files to inbox/ for re-ingestion:")

    moved = migrate(vault, args.dry_run)

    print("")
    if moved == 0:
        print("No deprecated files found — vault is already v3-compatible.")
    else:
        print(f"Moved {moved} file(s) to inbox/.")
        print("")
        print("Next step: run /secondbrain:dream-protocol (or /secondbrain:ingest)")
        print("to re-ingest the moved files. Ingest will route their content")
        print("into the appropriate places based on current routing rules.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
