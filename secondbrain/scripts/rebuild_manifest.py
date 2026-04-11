#!/usr/bin/env python3
"""
rebuild_manifest.py — Regenerate _MANIFEST.md for an Obsidian vault.

Python 3.8+, zero external dependencies.

Usage:
    python3 rebuild_manifest.py /path/to/vault
"""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple

EXCLUDED_DIRS = {".obsidian", ".git", ".trash", ".stfolder", "node_modules"}
SYSTEM_DIRS = {"brain", "entities", "me", "inbox", "archive", "scratch"}

LOG_ENTRY_RE = re.compile(
    r"^##\s+\[(\d{4}-\d{2}-\d{2})\s+\d{2}:\d{2}\]\s+(\S+)\s+\|\s+(.+)$"
)


def collect_md_files(vault: Path) -> List[Path]:
    results = []
    for root, dirs, files in os.walk(vault):
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
        for f in files:
            if f.endswith(".md"):
                results.append(Path(root) / f)
    return results


def count_per_directory(vault: Path, md_files: List[Path]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for f in md_files:
        rel = f.relative_to(vault)
        parts = rel.parts
        if len(parts) > 1:
            top = parts[0]
        else:
            continue
        counts[top] = counts.get(top, 0) + 1
    return dict(sorted(counts.items()))


def list_entities(vault: Path) -> List[Tuple[str, str]]:
    entities_dir = vault / "entities"
    if not entities_dir.is_dir():
        return []
    results = []
    for f in sorted(entities_dir.iterdir()):
        if f.suffix == ".md" and f.is_file():
            stem = f.stem
            display = stem.replace("-", " ").title()
            results.append((f"entities/{stem}", display))
    return results


def list_domains(vault: Path) -> List[str]:
    domains = []
    for entry in sorted(vault.iterdir()):
        if not entry.is_dir():
            continue
        name = entry.name
        if name.startswith(".") or name.startswith("_"):
            continue
        if name in EXCLUDED_DIRS or name in SYSTEM_DIRS:
            continue
        domains.append(name)
    return domains


def parse_recent_log_entries(vault: Path, days: int = 7) -> List[Tuple[str, str, str]]:
    log_path = vault / "log.md"
    if not log_path.is_file():
        return []

    cutoff = datetime.now().date() - timedelta(days=days)
    entries = []

    for line in log_path.read_text(encoding="utf-8").splitlines():
        m = LOG_ENTRY_RE.match(line.strip())
        if not m:
            continue
        date_str, operation, title = m.group(1), m.group(2), m.group(3).strip()
        try:
            entry_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if entry_date >= cutoff:
            entries.append((date_str, operation, title))

    return entries


def build_manifest(vault: Path) -> str:
    md_files = collect_md_files(vault)
    total = len(md_files)
    today = datetime.now().strftime("%Y-%m-%d")
    dir_counts = count_per_directory(vault, md_files)
    entities = list_entities(vault)
    domains = list_domains(vault)
    recent = parse_recent_log_entries(vault)

    lines = [
        "# Vault Manifest",
        "",
        f"**Files:** {total}",
        f"**Last updated:** {today}",
        "",
        "## Structure",
        "",
        "| Directory | Files |",
        "|-----------|-------|",
    ]
    for dirname, count in dir_counts.items():
        lines.append(f"| {dirname}/ | {count} |")

    lines.append("")
    lines.append("## Entities")
    lines.append("")
    if entities:
        for path, display in entities:
            lines.append(f"- [[{path}|{display}]]")
    else:
        lines.append("No entities found.")

    lines.append("")
    lines.append("## Domains")
    lines.append("")
    if domains:
        for d in domains:
            lines.append(f"- {d}/")
    else:
        lines.append("No domain folders found.")

    lines.append("")
    lines.append("## Recent Activity (Last 7 Days)")
    lines.append("")
    if recent:
        for date_str, operation, title in recent:
            lines.append(f"- [{date_str}] {operation}: {title}")
    else:
        lines.append("No recent activity.")

    lines.append("")
    return "\n".join(lines)


def write_manifest_atomic(vault: Path, content: str) -> None:
    target = vault / "_MANIFEST.md"
    tmp = vault / "_MANIFEST.md.tmp"
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(target)


def main(argv: List[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    if len(argv) != 1:
        print("Usage: python3 rebuild_manifest.py /path/to/vault", file=sys.stderr)
        return 1

    vault = Path(argv[0])
    if not vault.is_dir():
        print(f"Error: {vault} is not a directory", file=sys.stderr)
        return 1

    try:
        content = build_manifest(vault)
        write_manifest_atomic(vault, content)
        print(f"Wrote _MANIFEST.md ({vault / '_MANIFEST.md'})")
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
