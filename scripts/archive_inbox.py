#!/usr/bin/env python3
"""
archive_inbox.py — Move processed inbox files to archive in an Obsidian vault.

Python 3.8+, zero external dependencies.

Usage:
    python3 archive_inbox.py /path/to/vault
    python3 archive_inbox.py /path/to/vault --dry-run
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

PROCESSED_RE = re.compile(r"\[processed::\s*true\s*\]", re.IGNORECASE)
BINARY_SUFFIXES = {".pptx", ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".docx", ".xlsx", ".zip"}


def is_processed(path: Path) -> bool:
    text = path.read_text(encoding="utf-8", errors="replace")
    return bool(PROCESSED_RE.search(text))


def archive_dest(vault: Path, src: Path) -> Path:
    mtime = datetime.fromtimestamp(os.path.getmtime(src))
    month_dir = vault / "archive" / "inbox" / mtime.strftime("%Y-%m")
    return month_dir / src.name


def unique_dest(dest: Path) -> Path:
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    n = 1
    while True:
        candidate = dest.with_name(f"{stem}-{n}{suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def archive_inbox(vault: Path, dry_run: bool = False) -> Tuple[int, int, int]:
    inbox = vault / "inbox"
    if not inbox.is_dir():
        print(f"No inbox directory at {inbox}")
        return 0, 0, 0

    moved = 0
    skipped_unprocessed = 0
    skipped_binary = 0

    for entry in sorted(inbox.iterdir()):
        if not entry.is_file():
            continue

        if entry.suffix.lower() != ".md":
            print(f"  SKIP (binary): {entry.name}")
            skipped_binary += 1
            continue

        if not is_processed(entry):
            skipped_unprocessed += 1
            continue

        dest = unique_dest(archive_dest(vault, entry))

        if dry_run:
            print(f"  WOULD MOVE: {entry.name} -> {dest.relative_to(vault)}")
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(entry), str(dest))
            print(f"  MOVED: {entry.name} -> {dest.relative_to(vault)}")
        moved += 1

    return moved, skipped_unprocessed, skipped_binary


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Archive processed inbox files.")
    parser.add_argument("vault", type=Path, help="Path to Obsidian vault root")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be moved")
    args = parser.parse_args(argv)

    vault = args.vault.resolve()
    if not vault.is_dir():
        print(f"Error: vault path does not exist: {vault}", file=sys.stderr)
        return 1

    print(f"Archiving inbox in {vault}" + (" (dry run)" if args.dry_run else ""))
    moved, skipped_unprocessed, skipped_binary = archive_inbox(vault, dry_run=args.dry_run)

    print(f"\nSummary: {moved} moved, {skipped_unprocessed} skipped (unprocessed), {skipped_binary} skipped (binary)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
