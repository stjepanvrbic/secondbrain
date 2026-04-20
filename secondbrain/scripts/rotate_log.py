#!/usr/bin/env python3
"""
rotate_log.py — archive entries older than N days from `log.md`.

The plugin's append-only audit trail (`<vault>/log.md`) grows forever. Even
after the session-activity spam fix, a user who's been running the plugin
for months eventually accumulates MBs of historical entries that the agent
never references but every vault-scanning tool has to page through.

This script is the long-term hygiene step. It scans log.md entries (blocks
starting with `## [YYYY-MM-DD HH:MM] ...`), moves anything older than
`--max-age-days` into `<vault>/archive/log-YYYY-MM.md` (appended, grouped by
month), and rewrites log.md with the remaining recent entries.

Design:
  - Idempotent: running twice is a no-op when nothing is old enough.
  - Order-preserving: entries in the archive keep their original order.
  - Stdlib-only, zero external deps.
  - Fails LOUD, not SILENT: errors go to stderr with non-zero exit.

Invocation points:
  - dream-protocol Phase 5 (nightly): `--max-age-days 30`, no size gate.
  - SessionStart hook (belt-and-suspenders for broken dream-protocol):
    `--max-age-days 30 --max-size-mb 10` (noop unless log.md is >10 MB).

Exit codes:
  0 — success (including "nothing to rotate")
  1 — vault or log not found, or I/O error
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Entry header pattern: `## [<anything>] <operation> | <note>`
# Two groups — the full bracketed token (for display/logging) and an
# optional date prefix inside the brackets (used for age comparison).
# We intentionally match non-date brackets too so we don't silently drop
# malformed entries; rotate() keeps any entry whose date can't be parsed.
_HEADER_RE = re.compile(
    r"^## \[((\d{4}-\d{2}-\d{2})(?: \d{2}:\d{2})?|[^\]]*)\][^\n]*$",
    re.MULTILINE,
)


def _split_into_entries(content: str) -> tuple[str, list[tuple[str, str]]]:
    """Return (preamble, [(date, entry_text), ...]).

    An entry is a header line plus all subsequent lines up to (but not
    including) the next header. The preamble is everything before the
    first header (usually the `# Log` title plus a blank line).
    """
    matches = list(_HEADER_RE.finditer(content))
    if not matches:
        return content, []

    preamble = content[: matches[0].start()]
    entries: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        entry_text = content[start:end]
        # Group 2 is the parsed date; group 1 is the full bracket body (which
        # may or may not be a date). An empty group 2 means the date didn't
        # parse — caller should treat it as "unknown age, keep to be safe".
        date_str = m.group(2) or ""
        entries.append((date_str, entry_text))
    return preamble, entries


def _parse_date(s: str) -> datetime | None:
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        return None


def _group_by_month(
    entries: list[tuple[str, str]],
) -> dict[str, list[str]]:
    """Group entry texts by `YYYY-MM` derived from their header date.

    Entries with unparseable dates are grouped under an `unknown` bucket
    so nothing gets silently dropped.
    """
    buckets: dict[str, list[str]] = {}
    for date_str, text in entries:
        dt = _parse_date(date_str)
        key = dt.strftime("%Y-%m") if dt else "unknown"
        buckets.setdefault(key, []).append(text)
    return buckets


def rotate(
    vault: Path,
    *,
    max_age_days: int,
    max_size_mb: float | None,
    dry_run: bool,
) -> tuple[int, int]:
    """Archive old entries. Returns (moved_count, kept_count).

    When `max_size_mb` is set, rotation is skipped if log.md is smaller
    than the threshold — callers like the SessionStart hook use this to
    run nightly without thrashing small logs.
    """
    log = vault / "log.md"
    if not log.is_file():
        return 0, 0

    if max_size_mb is not None:
        size_mb = log.stat().st_size / (1024 * 1024)
        if size_mb < max_size_mb:
            return 0, 0

    content = log.read_text(encoding="utf-8")
    preamble, entries = _split_into_entries(content)
    if not entries:
        return 0, 0

    cutoff = datetime.now() - timedelta(days=max_age_days)
    old: list[tuple[str, str]] = []
    kept: list[tuple[str, str]] = []
    for date_str, text in entries:
        dt = _parse_date(date_str)
        if dt is None:
            # Unparseable date — keep it. Losing data to a bad format
            # match is worse than a slightly bigger log.
            kept.append((date_str, text))
            continue
        if dt < cutoff:
            old.append((date_str, text))
        else:
            kept.append((date_str, text))

    if not old:
        return 0, len(kept)

    if dry_run:
        return len(old), len(kept)

    # Archive old entries grouped by month. Append — never overwrite —
    # so multiple rotate runs in the same month accumulate correctly.
    archive_dir = vault / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    for month, texts in _group_by_month(old).items():
        archive_path = archive_dir / f"log-{month}.md"
        header = f"# Log archive — {month}\n\n" if not archive_path.exists() else ""
        block = "".join(texts)
        if not block.endswith("\n"):
            block += "\n"
        with archive_path.open("a", encoding="utf-8") as fh:
            fh.write(header + block)

    # Rewrite log.md with preamble + kept entries only.
    new_content = preamble + "".join(text for _, text in kept)
    log.write_text(new_content, encoding="utf-8")
    return len(old), len(kept)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--vault",
        required=True,
        type=Path,
        help="Vault root. log.md lives at <vault>/log.md.",
    )
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=30,
        help="Entries older than this go to archive/log-YYYY-MM.md (default 30).",
    )
    parser.add_argument(
        "--max-size-mb",
        type=float,
        default=None,
        help=(
            "If set, rotate only when log.md is larger than this (in MB). "
            "Used by SessionStart hook as a belt-and-suspenders guard."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would rotate without writing.",
    )
    args = parser.parse_args(argv)

    vault: Path = args.vault.expanduser().resolve()
    if not vault.is_dir():
        print(f"error: vault not found: {vault}", file=sys.stderr)
        return 1

    try:
        moved, kept = rotate(
            vault,
            max_age_days=args.max_age_days,
            max_size_mb=args.max_size_mb,
            dry_run=args.dry_run,
        )
    except OSError as exc:
        print(f"error: rotate failed: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"rotate_log: would move {moved} entries, keep {kept} recent")
    else:
        print(f"rotate_log: moved {moved} entries to archive, kept {kept} recent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
