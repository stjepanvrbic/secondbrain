#!/usr/bin/env python3
"""
vault_lookup_cwd.py — cwd-to-entity matcher for the SessionStart hook.

The session-start hot-memory emitter calls this on every session start.
Given the current working directory, it tries to match it to a vault entity
and — if a match is found — emits an `## Active Project Context` markdown
section ready to append to the hot-memory systemMessage.

Matching logic (Q39 hybrid, per plan):

  1. Frontmatter match — if an entity's YAML frontmatter has a `paths:`
     field (list of absolute paths) and `cwd` equals or is under any of
     them, that's the match.

  2. Fuzzy basename match (fallback) — if no frontmatter match exists,
     compare `Path(cwd).name` (case-insensitive, `.md` stripped) against
     entity filenames.

  3. Frontmatter always wins over fuzzy. If multiple entities have a
     frontmatter path match, the one whose declared path is deepest (the
     most specific) wins. If multiple fuzzy matches exist, the first one
     alphabetically wins.

  4. No match → empty stdout, exit 0. Missing peripheral files
     (brain/status.md, log.md) are tolerated — the corresponding sections
     just get skipped.

Performance: this script runs on every session start, so it must be fast
(<100ms target). We read vault files via filesystem only — NO MCP. We do
not shell out to grep. We do not parse full YAML — a deliberately minimal
frontmatter reader handles the `key: value` / `paths: [...]` / `paths:\\n  -
...` shapes entities use in practice.

Stdlib-only, Python 3.8+. No external dependencies.

Usage:
    python3 vault_lookup_cwd.py --vault <vault_path> --cwd <cwd>

Exit codes:
    0  success (match or no match are both "success")
    1  operator error (vault path doesn't exist)
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Frontmatter parsing — minimal by design
# ---------------------------------------------------------------------------

def _split_frontmatter(content: str) -> Tuple[Optional[Dict[str, object]], str]:
    """Extract a YAML-ish frontmatter dict and the remaining body.

    Returns `(fields, body)`. `fields` is None if there is no frontmatter.

    Supported shapes (everything else is best-effort):
      - `key: value`        → str
      - `key: [a, b, c]`    → list[str]
      - `key:` followed by  → list[str]
        `  - value`
    """
    if not content.startswith("---"):
        return None, content

    lines = content.splitlines(keepends=True)
    closing: Optional[int] = None
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r\n") == "---":
            closing = i
            break
    if closing is None:
        return None, content

    raw_fm_lines = [line.rstrip("\r\n") for line in lines[1:closing]]
    body = "".join(lines[closing + 1:])

    fields: Dict[str, object] = {}
    i = 0
    while i < len(raw_fm_lines):
        line = raw_fm_lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        if ":" not in line:
            i += 1
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value_stripped = value.strip()

        if not value_stripped:
            # Block list: collect subsequent `  - ...` lines.
            items: List[str] = []
            j = i + 1
            while j < len(raw_fm_lines):
                sub = raw_fm_lines[j]
                if sub.startswith("  -") or sub.startswith("- "):
                    item = sub.lstrip().lstrip("-").strip()
                    if item:
                        items.append(item)
                    j += 1
                elif sub.strip() == "":
                    j += 1
                elif sub.startswith("  "):
                    # A sub-field inside a dict — not our use case. Skip.
                    j += 1
                else:
                    break
            if items:
                fields[key] = items
                i = j
                continue
            fields[key] = ""
            i += 1
            continue

        if value_stripped.startswith("[") and value_stripped.endswith("]"):
            inner = value_stripped[1:-1]
            items = [seg.strip() for seg in inner.split(",") if seg.strip()]
            fields[key] = items
        else:
            fields[key] = value_stripped
        i += 1

    return fields, body


def _frontmatter_paths(fields: Dict[str, object]) -> List[str]:
    """Return the `paths:` frontmatter field as a list of strings (or [])."""
    raw = fields.get("paths")
    if isinstance(raw, list):
        return [str(p) for p in raw if str(p).strip()]
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    return []


def _frontmatter_summary(fields: Dict[str, object]) -> Optional[str]:
    raw = fields.get("summary")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


# ---------------------------------------------------------------------------
# Match scoring
# ---------------------------------------------------------------------------

def _cwd_is_under(cwd: Path, declared: str) -> bool:
    """True if `cwd` equals or is a descendant of `declared`."""
    try:
        decl = Path(declared).expanduser()
    except (TypeError, ValueError):
        return False
    try:
        cwd_resolved = cwd.resolve()
        decl_resolved = decl.resolve()
    except OSError:
        cwd_resolved = cwd
        decl_resolved = decl
    try:
        cwd_resolved.relative_to(decl_resolved)
        return True
    except ValueError:
        return False


def _find_frontmatter_match(
    entities_dir: Path,
    cwd: Path,
) -> Optional[Tuple[str, Dict[str, object], str]]:
    """Return (entity_name, frontmatter_fields, body) or None.

    Scans `entities_dir` for `.md` files whose `paths:` frontmatter contains
    the cwd (or an ancestor of it). If multiple match, the one with the
    deepest declared path wins — "most specific wins" beats the common case
    where a parent project folder is declared on one entity and a sub-project
    lives under it.
    """
    if not entities_dir.is_dir():
        return None

    best: Optional[Tuple[int, str, Dict[str, object], str]] = None
    for entity_file in sorted(entities_dir.glob("*.md")):
        try:
            content = entity_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fields, body = _split_frontmatter(content)
        if fields is None:
            continue
        paths = _frontmatter_paths(fields)
        if not paths:
            continue

        for declared in paths:
            if _cwd_is_under(cwd, declared):
                decl_depth = len(Path(declared).expanduser().parts)
                if best is None or decl_depth > best[0]:
                    best = (decl_depth, entity_file.stem, fields, body)
                break

    if best is None:
        return None
    name, fields, body = best[1], best[2], best[3]
    return name, fields, body


def _find_fuzzy_match(
    entities_dir: Path,
    cwd: Path,
) -> Optional[Tuple[str, Dict[str, object], str]]:
    """Case-insensitive basename match against entity filenames.

    Returns (entity_name, frontmatter_fields, body) or None.
    """
    if not entities_dir.is_dir():
        return None

    cwd_name = cwd.name.casefold()
    if not cwd_name:
        return None

    for entity_file in sorted(entities_dir.glob("*.md")):
        if entity_file.stem.casefold() == cwd_name:
            try:
                content = entity_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            fields, body = _split_frontmatter(content)
            if fields is None:
                fields = {}
            return entity_file.stem, fields, body

    return None


# ---------------------------------------------------------------------------
# Body extraction for the summary line
# ---------------------------------------------------------------------------

def _first_body_line(body: str) -> Optional[str]:
    """Return the first non-empty, non-H1 line from a markdown body."""
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        # Skip Dataview inline-field lines (noise, not prose).
        if stripped.startswith("- [") or stripped.startswith("[") and "::" in stripped:
            continue
        return stripped
    return None


# ---------------------------------------------------------------------------
# Related status tasks and log entries
# ---------------------------------------------------------------------------

_ENTITY_WIKILINK_RE = re.compile(r"\[\[entities/([^\]|#]+)(?:[|#][^\]]*)?\]\]")


def _status_tasks_for_entity(status_path: Path, entity_name: str) -> List[str]:
    """Return open-task lines from `brain/status.md` that reference the entity.

    Matches `[[entities/<entity_name>...]]` wikilinks in incomplete `- [ ]`
    task lines. Case-insensitive on the entity slug. Only incomplete tasks
    (leading `- [ ]`) are returned; done tasks are skipped. Returns up to 5
    lines so the section doesn't balloon.
    """
    if not status_path.is_file():
        return []
    try:
        content = status_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    target = entity_name.casefold()
    out: List[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- [ ]"):
            continue
        refs = _ENTITY_WIKILINK_RE.findall(stripped)
        if any(ref.casefold() == target for ref in refs):
            out.append(stripped)
            if len(out) >= 5:
                break
    return out


def _recent_log_entries_for_entity(log_path: Path, entity_name: str) -> List[str]:
    """Return the last 3 session log H2-headers whose section body mentions
    the entity. Returns just the H2 header lines (most recent first).
    """
    if not log_path.is_file():
        return []
    try:
        content = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    target = entity_name.casefold()
    entries: List[Tuple[str, str]] = []
    current_header: Optional[str] = None
    current_body: List[str] = []
    for line in content.splitlines():
        if line.startswith("## "):
            if current_header is not None:
                entries.append((current_header, "\n".join(current_body)))
            current_header = line.strip()
            current_body = []
        else:
            if current_header is not None:
                current_body.append(line)
    if current_header is not None:
        entries.append((current_header, "\n".join(current_body)))

    # Walk newest-first (log.md appends chronologically, latest last).
    matches: List[str] = []
    for header, body in reversed(entries):
        if target in body.casefold() or target in header.casefold():
            matches.append(header)
            if len(matches) >= 3:
                break
    return matches


# ---------------------------------------------------------------------------
# Public: build the markdown section
# ---------------------------------------------------------------------------

def build_active_project_section(
    vault_path: Path,
    cwd: Path,
) -> str:
    """Return an `## Active Project Context` markdown section, or "" if
    no entity matches the cwd.
    """
    entities_dir = vault_path / "entities"
    if not entities_dir.is_dir():
        return ""

    match = _find_frontmatter_match(entities_dir, cwd)
    if match is None:
        match = _find_fuzzy_match(entities_dir, cwd)
    if match is None:
        return ""

    entity_name, fields, body = match
    summary = _frontmatter_summary(fields) or _first_body_line(body)

    parts: List[str] = []
    parts.append("")
    parts.append("## Active Project Context")
    parts.append("")
    parts.append(
        "You are working in `" + str(cwd) + "`. "
        "This matches `[[entities/" + entity_name + "]]`."
    )
    parts.append("")
    if summary:
        parts.append("> " + summary)
        parts.append("")

    tasks = _status_tasks_for_entity(vault_path / "brain" / "status.md", entity_name)
    if tasks:
        parts.append("Open tasks for this entity:")
        for task in tasks:
            parts.append(task)
        parts.append("")

    log_entries = _recent_log_entries_for_entity(vault_path / "log.md", entity_name)
    if log_entries:
        parts.append("Recent log.md entries mentioning this entity (last 3):")
        for entry in log_entries:
            parts.append("- " + entry.lstrip("# ").strip())
        parts.append("")

    return "\n".join(parts).rstrip() + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vault_lookup_cwd.py",
        description=(
            "Match the current working directory to a vault entity and "
            "emit an Active Project Context markdown section. Used by the "
            "SessionStart hook."
        ),
    )
    parser.add_argument(
        "--vault",
        required=True,
        help="Absolute path to the vault root.",
    )
    parser.add_argument(
        "--cwd",
        required=True,
        help="The current working directory to match against vault entities.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    vault_path = Path(args.vault).expanduser()
    if not vault_path.is_dir():
        sys.stderr.write(f"error: vault path does not exist: {vault_path}\n")
        return 1

    cwd = Path(args.cwd).expanduser()

    section = build_active_project_section(vault_path, cwd)
    if section:
        sys.stdout.write(section)
    return 0


if __name__ == "__main__":
    sys.exit(main())
