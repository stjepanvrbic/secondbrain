#!/usr/bin/env python3
"""
vault_guide.py — Generate a dynamic vault summary for session context.

Produces a concise overview of the vault's current state: file counts,
active tasks, deadlines, entity stats, inbox status, and known issues.

Usage:
    python3 vault_guide.py /path/to/vault [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

EXCLUDED_DIRS = {".git", ".obsidian", ".trash", ".stfolder", ".tmp.driveupload", ".stversions", "node_modules"}
TASK_RE = re.compile(r"^\s*- \[ \] ")
DUE_RE = re.compile(r"\[due::\s*(\d{4}-\d{2}-\d{2})\s*\]")
LOG_ENTRY_RE = re.compile(r"^## \[(\d{4}-\d{2}-\d{2})\s+\d{2}:\d{2}\]\s+(\S+)\s*\|\s*(.+)$")
WIKILINK_RE = re.compile(r"\[\[([^\]|]+)")
PROCESSED_INLINE_RE = re.compile(r"\[processed::\s*true\s*\]", re.IGNORECASE)


def _has_processed_marker(text: str) -> bool:
    """Check for 'processed: true' in either inline Dataview or YAML frontmatter."""
    if PROCESSED_INLINE_RE.search(text):
        return True
    if text.lstrip().startswith("---"):
        in_frontmatter = False
        for line in text.split("\n"):
            stripped = line.rstrip()
            if stripped == "---":
                if not in_frontmatter:
                    in_frontmatter = True
                    continue
                break
            if in_frontmatter and re.match(r"^processed:\s*true\s*$", stripped, re.IGNORECASE):
                return True
    return False


def count_files(vault: Path) -> Dict[str, int]:
    """Count .md files per top-level directory."""
    counts: Dict[str, int] = {}
    for dirpath, dirnames, filenames in os.walk(vault):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]
        rel = os.path.relpath(dirpath, vault)
        top = rel.split(os.sep)[0] if rel != "." else "(root)"
        md_count = sum(1 for f in filenames if f.endswith(".md"))
        counts[top] = counts.get(top, 0) + md_count
    return counts


def count_entities_by_links(vault: Path) -> List[Tuple[str, int]]:
    """Count incoming wikilinks to each entity, return sorted by count descending."""
    entity_dir = vault / "entities"
    if not entity_dir.is_dir():
        return []

    entity_stems = {f.stem.lower() for f in entity_dir.iterdir() if f.suffix == ".md"}
    incoming: Counter[str] = Counter()

    for dirpath, dirnames, filenames in os.walk(vault):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]
        for fname in filenames:
            if not fname.endswith(".md"):
                continue
            path = Path(dirpath) / fname
            if path.parent == entity_dir:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for m in WIKILINK_RE.finditer(text):
                target = m.group(1).split("#")[0].strip()
                if target.startswith("entities/"):
                    stem = target.removeprefix("entities/").removesuffix(".md").lower()
                    if stem in entity_stems:
                        display = stem.replace("-", " ").title()
                        incoming[display] += 1

    return incoming.most_common()


def get_active_tasks(vault: Path) -> List[str]:
    """Return open task lines from brain/status.md."""
    status = vault / "brain" / "status.md"
    if not status.exists():
        return []
    try:
        lines = status.read_text(encoding="utf-8", errors="replace").split("\n")
    except OSError:
        return []
    return [line.strip() for line in lines if TASK_RE.match(line)]


def get_upcoming_deadlines(vault: Path, days: int = 7) -> List[Tuple[str, str]]:
    """Return (date, task_text) for tasks due within N days."""
    today = datetime.now().strftime("%Y-%m-%d")
    cutoff = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")

    upcoming: List[Tuple[str, str]] = []
    for name in ("brain/status.md", "brain/deadlines.md"):
        path = vault / name
        if not path.exists():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").split("\n")
        except OSError:
            continue
        for line in lines:
            due = DUE_RE.search(line)
            if due and today <= due.group(1) <= cutoff and TASK_RE.match(line):
                upcoming.append((due.group(1), line.strip()))

    return sorted(upcoming)


def count_inbox(vault: Path) -> Tuple[int, int]:
    """Return (total, unprocessed) counts for inbox/ files."""
    inbox = vault / "inbox"
    if not inbox.is_dir():
        return 0, 0
    total = unprocessed = 0
    for f in inbox.iterdir():
        if not f.suffix == ".md":
            continue
        total += 1
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
            if not _has_processed_marker(text):
                unprocessed += 1
        except OSError:
            unprocessed += 1
    return total, unprocessed


def get_last_dream_run(vault: Path) -> Optional[str]:
    """Find the most recent dream-protocol entry in log.md."""
    log = vault / "log.md"
    if not log.exists():
        return None
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").split("\n")
    except OSError:
        return None
    last = None
    for line in lines:
        m = LOG_ENTRY_RE.match(line)
        if m and m.group(2) == "dream-protocol":
            last = m.group(1)
    return last


def run_verify(vault: Path) -> Optional[Dict]:
    """Run verify_vault.py and return summary, or None on failure."""
    script = Path(__file__).parent / "verify_vault.py"
    if not script.exists():
        return None
    try:
        result = subprocess.run(
            [sys.executable, str(script), str(vault), "--json", "--quiet"],
            capture_output=True, text=True, timeout=30,
        )
        data = json.loads(result.stdout)
        return data.get("summary", {})
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return None


def generate_guide(vault: Path) -> Dict:
    """Generate the full vault guide as a structured dict."""
    file_counts = count_files(vault)
    total_files = sum(file_counts.values())
    entities = count_entities_by_links(vault)
    active_tasks = get_active_tasks(vault)
    deadlines = get_upcoming_deadlines(vault)
    inbox_total, inbox_unprocessed = count_inbox(vault)
    last_dream = get_last_dream_run(vault)
    issues = run_verify(vault)

    return {
        "total_files": total_files,
        "files_by_directory": file_counts,
        "entity_count": len(entities),
        "top_entities": entities[:5],
        "active_tasks": len(active_tasks),
        "upcoming_deadlines": [{"date": d, "task": t} for d, t in deadlines],
        "inbox_total": inbox_total,
        "inbox_unprocessed": inbox_unprocessed,
        "last_dream_protocol": last_dream,
        "issues": issues,
    }


def format_human(guide: Dict) -> str:
    """Format guide as human-readable text for session context."""
    lines = [
        f"Vault: {guide['total_files']} files",
        "",
        "Files by directory:",
    ]
    for dir_name, count in sorted(guide["files_by_directory"].items()):
        lines.append(f"  {dir_name + '/':20s} {count}")

    lines.append(f"\nEntities: {guide['entity_count']}")
    if guide["top_entities"]:
        lines.append("Most-linked:")
        for name, count in guide["top_entities"]:
            lines.append(f"  {name} ({count} links)")

    lines.append(f"\nActive tasks: {guide['active_tasks']}")

    if guide["upcoming_deadlines"]:
        lines.append("\nUpcoming deadlines (7 days):")
        for dl in guide["upcoming_deadlines"]:
            lines.append(f"  [{dl['date']}] {dl['task'][:80]}")
    else:
        lines.append("\nNo upcoming deadlines.")

    lines.append(f"\nInbox: {guide['inbox_total']} total, {guide['inbox_unprocessed']} unprocessed")

    if guide["last_dream_protocol"]:
        lines.append(f"Last dream-protocol: {guide['last_dream_protocol']}")
    else:
        lines.append("Last dream-protocol: never run")

    if guide["issues"]:
        s = guide["issues"]
        lines.append(f"\nKnown issues: {s.get('errors', 0)} errors, {s.get('warnings', 0)} warnings")
    else:
        lines.append("\nKnown issues: unable to check")

    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Generate dynamic vault summary")
    p.add_argument("vault", help="Path to vault root")
    p.add_argument("--json", action="store_true", dest="json_output", help="Output as JSON")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    vault = Path(args.vault).expanduser().resolve()

    if not vault.is_dir():
        print(f"Error: vault not found: {args.vault}", file=sys.stderr)
        return 1

    guide = generate_guide(vault)

    if args.json_output:
        print(json.dumps(guide, indent=2))
    else:
        print(format_human(guide))

    return 0


if __name__ == "__main__":
    sys.exit(main())
