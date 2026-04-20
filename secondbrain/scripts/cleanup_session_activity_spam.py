#!/usr/bin/env python3
"""
cleanup_session_activity_spam.py — remove legacy `session-activity | checkpoint`
entries from a vault's log.md.

A prior version of `hooks/emit-hot-memory.sh` appended one such entry on every
SessionStart (startup / clear / compact), which Claude Code fires frequently.
On one user's vault this produced ~16k entries/day, 97k total over a week,
bloating log.md past 180k lines and choking every tool that touched it.

The offending append has been deleted from the hook. This script cleans up the
accumulated noise. Idempotent, stdlib-only, safe to run repeatedly.

Usage:

    python3 cleanup_session_activity_spam.py --vault /path/to/vault
    python3 cleanup_session_activity_spam.py --vault ... --dry-run
    python3 cleanup_session_activity_spam.py --vault ... --threshold 10000

`--dry-run` reports counts without writing. `--threshold N` is a noop when the
match count is below N — dream-protocol uses this to auto-heal only severe
bloat while leaving trivial residue for the user to clear via doctor.

Exit codes:
  0 — success (including noop under threshold, or no matches)
  1 — usage error or vault not found
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Match the exact header line the old hook emitted. We match the WHOLE line
# (including the trailing newline) so we can strip it cleanly without leaving
# blank gaps where entries used to be.
#
# Format: "## [YYYY-MM-DD HH:MM] session-activity | checkpoint"
_SPAM_RE = re.compile(
    r"^## \[\d{4}-\d{2}-\d{2} \d{2}:\d{2}\] session-activity \| checkpoint\s*\n?",
    re.MULTILINE,
)


def _count_matches(content: str) -> int:
    return len(_SPAM_RE.findall(content))


def clean(content: str) -> tuple[str, int]:
    """Return (cleaned_content, matches_removed).

    Strips the spam lines AND any immediately-following blank line, so the log
    doesn't develop run-away whitespace. Real log entries are header + body +
    blank-line separator; the spam entries had no body, just a leading blank
    line, so we tighten that up too.
    """
    matches = _count_matches(content)
    if matches == 0:
        return content, 0

    cleaned = _SPAM_RE.sub("", content)
    # Collapse any runs of 3+ blank lines into a single blank line — the
    # original log would never have 3 blanks in a row except where spam
    # entries used to be back-to-back.
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned, matches


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--vault",
        required=True,
        type=Path,
        help="Path to the vault root (log.md must live at <vault>/log.md).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report counts without writing.",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=0,
        help=(
            "If the match count is below THRESHOLD, exit 0 without writing. "
            "Used by dream-protocol to auto-heal only severe bloat."
        ),
    )
    args = parser.parse_args(argv)

    vault: Path = args.vault
    if not vault.is_dir():
        print(f"error: vault not found: {vault}", file=sys.stderr)
        return 1

    log = vault / "log.md"
    if not log.is_file():
        print(f"cleanup_session_activity_spam: no log.md at {log}; nothing to do")
        return 0

    original = log.read_text(encoding="utf-8")
    cleaned, matches = clean(original)

    if matches == 0:
        print("cleanup_session_activity_spam: no matches; log is clean")
        return 0

    if matches < args.threshold:
        print(
            f"cleanup_session_activity_spam: {matches} matches < threshold "
            f"{args.threshold}; leaving log untouched"
        )
        return 0

    if args.dry_run:
        print(
            f"cleanup_session_activity_spam: would remove {matches} entries "
            f"from {log} (dry-run)"
        )
        return 0

    log.write_text(cleaned, encoding="utf-8")
    print(f"cleanup_session_activity_spam: removed {matches} entries from {log}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
