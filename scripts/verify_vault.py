#!/usr/bin/env python3
"""
verify_vault.py — Obsidian vault health checker.

Runs structural, link, metadata, and hygiene checks against an Obsidian vault.
Python 3.8+, zero external dependencies.

Usage:
    python3 verify_vault.py /path/to/vault
    python3 verify_vault.py /path/to/vault --check wikilinks,metadata,duplicates
    python3 verify_vault.py /path/to/vault --modified-only brain/status.md entities/alice.md
    python3 verify_vault.py /path/to/vault --fix
    python3 verify_vault.py /path/to/vault --json --quiet
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class Issue:
    check: str
    severity: str      # "error", "warning", "info"
    file: str
    line: int          # 0 if not applicable
    message: str
    suggestion: str    # "" if none


@dataclasses.dataclass
class CheckResult:
    name: str
    issues: List[Issue]
    stats: Dict[str, int]


# ---------------------------------------------------------------------------
# Text processing helpers
# ---------------------------------------------------------------------------

class TextProcessor:
    """Strip markdown constructs that shouldn't be checked for wikilinks."""

    @staticmethod
    def strip_frontmatter(text: str) -> str:
        if not text.startswith("---"):
            return text
        lines = text.split("\n")
        if lines[0].rstrip() != "---":
            return text
        for i in range(1, len(lines)):
            if lines[i].rstrip() == "---":
                for j in range(i + 1):
                    lines[j] = ""
                return "\n".join(lines)
        return text

    @staticmethod
    def strip_code_blocks(text: str) -> str:
        lines = text.split("\n")
        result: List[str] = []
        in_fence = False
        fence_char = ""
        fence_count = 0
        for line in lines:
            stripped = line.lstrip()
            if not in_fence:
                if stripped.startswith("```") or stripped.startswith("~~~"):
                    fence_char = stripped[0]
                    fence_count = sum(1 for ch in stripped if ch == fence_char)
                    if fence_count >= 3:
                        in_fence = True
                        result.append("")
                        continue
                result.append(line)
            else:
                if stripped.startswith(fence_char * fence_count):
                    count = sum(1 for ch in stripped if ch == fence_char)
                    rest = stripped[count:].strip()
                    if count >= fence_count and rest == "":
                        in_fence = False
                result.append("")
        return "\n".join(result)

    @staticmethod
    def strip_inline_code(text: str) -> str:
        return re.sub(r"`+[^`\n]+`+", lambda m: " " * len(m.group(0)), text)

    @classmethod
    def clean(cls, text: str) -> str:
        """Apply all stripping in sequence."""
        text = cls.strip_frontmatter(text)
        text = cls.strip_code_blocks(text)
        return cls.strip_inline_code(text)


# ---------------------------------------------------------------------------
# Vault index — built once, shared by all checkers
# ---------------------------------------------------------------------------

EXCLUDED_DIRS = {
    ".git", ".obsidian", ".trash", ".stfolder",
    ".tmp.driveupload", ".stversions", "node_modules",
}

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)(?:\s+#+)?$", re.MULTILINE)
WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


class VaultIndex:
    """Index of all .md files in the vault."""

    def __init__(self, vault_root: Path, extra_skip: Optional[Set[str]] = None):
        self.vault_root = vault_root
        self.extra_skip = extra_skip or set()

        self.stem_to_paths: Dict[str, List[Path]] = {}
        self.path_to_headings: Dict[Path, List[Tuple[int, str]]] = {}  # (level, text)
        self.relative_to_abs: Dict[str, Path] = {}
        self.all_paths: List[Path] = []

        self._build()

    def _build(self) -> None:
        for dirpath, dirnames, filenames in os.walk(self.vault_root):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS and d not in self.extra_skip]
            for fname in filenames:
                if not fname.endswith(".md"):
                    continue
                abs_path = Path(dirpath) / fname
                self.all_paths.append(abs_path)

                rel_str = self.rel(abs_path)
                self.relative_to_abs[rel_str] = abs_path

                stem_lower = abs_path.stem.lower()
                self.stem_to_paths.setdefault(stem_lower, []).append(abs_path)

                self.path_to_headings[abs_path] = self._extract_headings(abs_path)

    @staticmethod
    def _extract_headings(path: Path) -> List[Tuple[int, str]]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        text = TextProcessor.strip_code_blocks(text)
        return [(len(m.group(1)), m.group(2)) for m in HEADING_RE.finditer(text)]

    def rel(self, abs_path: Path) -> str:
        try:
            return str(abs_path.relative_to(self.vault_root)).replace(os.sep, "/")
        except ValueError:
            return str(abs_path)

    def scope_to(self, files: List[str]) -> "VaultIndex":
        """Return a view scoped to only the given relative paths (+ their link targets)."""
        scoped = VaultIndex.__new__(VaultIndex)
        scoped.vault_root = self.vault_root
        scoped.extra_skip = self.extra_skip
        scoped.stem_to_paths = self.stem_to_paths
        scoped.path_to_headings = self.path_to_headings
        scoped.relative_to_abs = self.relative_to_abs

        target_paths: Set[Path] = set()
        for f in files:
            if f in self.relative_to_abs:
                target_paths.add(self.relative_to_abs[f])

        # Also include link targets from scoped files
        for abs_path in list(target_paths):
            try:
                text = abs_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            cleaned = TextProcessor.clean(text)
            for m in WIKILINK_RE.finditer(cleaned):
                target, _ = parse_wikilink(m.group(1))
                resolved = resolve_wikilink(target, self)
                if resolved:
                    target_paths.add(resolved)

        scoped.all_paths = [p for p in self.all_paths if p in target_paths]
        return scoped


# ---------------------------------------------------------------------------
# Wikilink resolution
# ---------------------------------------------------------------------------

def parse_wikilink(raw: str) -> Tuple[str, str]:
    """Parse '[[target|alias]]' or '[[target#section]]' into (target, section)."""
    target = raw.split("|")[0].strip()
    if "#" in target:
        parts = target.split("#", 1)
        return parts[0].strip(), parts[1].strip()
    return target, ""


def resolve_wikilink(target: str, index: VaultIndex) -> Optional[Path]:
    """Resolve a wikilink target to an absolute path, or None."""
    if not target:
        return None

    # 1. Exact relative path (with/without .md)
    for cand in [target, target + ".md"]:
        if cand in index.relative_to_abs:
            return index.relative_to_abs[cand]

    # 2. Stem match (case-insensitive)
    stem_lower = target.lower().removesuffix(".md").rsplit("/", 1)[-1]
    matches = index.stem_to_paths.get(stem_lower, [])
    if len(matches) == 1:
        return matches[0]
    for m in matches:
        rel_no_ext = index.rel(m).rsplit(".", 1)[0]
        if rel_no_ext.lower().endswith(target.lower()):
            return m
    if matches:
        return matches[0]

    # 3. Suffix match
    target_lower = target.lower()
    if not target_lower.endswith(".md"):
        target_lower += ".md"
    for rel_str, abs_path in index.relative_to_abs.items():
        if rel_str.lower().endswith(target_lower):
            return abs_path

    return None


# ---------------------------------------------------------------------------
# Checkers
# ---------------------------------------------------------------------------

class BrokenWikilinkChecker:
    """Detect broken wikilinks (missing target file or section)."""

    NAME = "wikilinks"

    def run(self, index: VaultIndex) -> CheckResult:
        issues: List[Issue] = []
        total_links = broken_files = broken_sections = 0

        for abs_path in index.all_paths:
            try:
                text = abs_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rel = index.rel(abs_path)
            cleaned = TextProcessor.clean(text)

            for line_no, line in enumerate(cleaned.split("\n"), start=1):
                for m in WIKILINK_RE.finditer(line):
                    total_links += 1
                    target, section = parse_wikilink(m.group(1))
                    if section.startswith("^"):
                        continue

                    resolved = resolve_wikilink(target, index)
                    if resolved is None and target:
                        broken_files += 1
                        issues.append(Issue(
                            check=self.NAME, severity="error", file=rel, line=line_no,
                            message=f"Broken wikilink: [[{m.group(1)}]] — target not found",
                            suggestion=self._suggest(target, index),
                        ))
                    elif resolved and section:
                        headings = [h for _, h in index.path_to_headings.get(resolved, [])]
                        if not any(h.lower() == section.lower() for h in headings):
                            broken_sections += 1
                            issues.append(Issue(
                                check=self.NAME, severity="warning", file=rel, line=line_no,
                                message=f"Missing section: [[{m.group(1)}]] — heading '{section}' not found",
                                suggestion="",
                            ))

        return CheckResult(
            name=self.NAME, issues=issues,
            stats={"total_links": total_links, "broken_files": broken_files, "broken_sections": broken_sections},
        )

    @staticmethod
    def _suggest(target: str, index: VaultIndex) -> str:
        stem = target.lower().rsplit("/", 1)[-1].removesuffix(".md")
        candidates = [s for s in index.stem_to_paths if stem in s or s in stem]
        return f"Did you mean: {', '.join(candidates[:3])}?" if candidates else ""


class MetadataValidator:
    """Validate task metadata in brain/status.md."""

    NAME = "metadata"
    TARGET = "brain/status.md"
    ENERGY_VALUES = {"low", "medium", "high"}
    EST_VALUES = {"5min", "10min", "15min", "30min", "1hr", "2hr"}

    TASK_RE = re.compile(r"^\s*- \[[ x]\] ")
    DUE_RE = re.compile(r"\[due::\s*(\S+)\s*\]")
    ENERGY_RE = re.compile(r"\[energy::\s*(\S+)\s*\]")
    EST_RE = re.compile(r"\[est::\s*(\S+)\s*\]")
    DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)")

    def run(self, index: VaultIndex) -> CheckResult:
        target_path = index.vault_root / self.TARGET
        if not target_path.exists():
            return CheckResult(
                name=self.NAME,
                issues=[Issue(self.NAME, "warning", self.TARGET, 0, f"{self.TARGET} not found", "")],
                stats={"tasks_checked": 0},
            )

        try:
            lines = target_path.read_text(encoding="utf-8", errors="replace").split("\n")
        except OSError:
            return CheckResult(name=self.NAME, issues=[], stats={"tasks_checked": 0})

        issues: List[Issue] = []
        section = "(top-level)"
        tasks_checked = 0

        for line_no, line in enumerate(lines, start=1):
            hm = self.HEADING_RE.match(line)
            if hm:
                section = hm.group(2).strip()
                continue
            if not self.TASK_RE.match(line):
                continue

            tasks_checked += 1
            is_open = "- [ ]" in line

            for regex, name, valid in [
                (self.DUE_RE, "due date", lambda v: self.DATE_RE.match(v)),
                (self.ENERGY_RE, "energy", lambda v: v.lower() in self.ENERGY_VALUES),
                (self.EST_RE, "estimate", lambda v: v.lower() in self.EST_VALUES),
            ]:
                m = regex.search(line)
                if m and not valid(m.group(1)):
                    issues.append(Issue(
                        self.NAME, "error", self.TARGET, line_no,
                        f"Invalid {name} '{m.group(1)}' in section '{section}'",
                        f"Expected: {'YYYY-MM-DD' if name == 'due date' else ', '.join(sorted(self.ENERGY_VALUES if name == 'energy' else self.EST_VALUES))}",
                    ))

            if is_open and not any(r.search(line) for r in [self.DUE_RE, self.ENERGY_RE, self.EST_RE]):
                issues.append(Issue(
                    self.NAME, "warning", self.TARGET, line_no,
                    f"Open task with no metadata in section '{section}'",
                    "Add [due:: ...], [energy:: ...], or [est:: ...]",
                ))

        return CheckResult(name=self.NAME, issues=issues, stats={"tasks_checked": tasks_checked})


class DuplicateHeadingChecker:
    """Detect duplicate headings at the same level within a file."""

    NAME = "duplicates"

    def run(self, index: VaultIndex) -> CheckResult:
        issues: List[Issue] = []
        files_with_dupes = 0

        for abs_path in index.all_paths:
            seen: Dict[Tuple[int, str], int] = {}
            file_has_dupe = False

            try:
                text = abs_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            cleaned = TextProcessor.strip_code_blocks(text)
            lines = cleaned.split("\n")

            for line_no, line in enumerate(lines, start=1):
                m = HEADING_RE.match(line)
                if not m:
                    continue
                level = len(m.group(1))
                title = m.group(2).strip()
                key = (level, title.lower())

                if key in seen:
                    if not file_has_dupe:
                        files_with_dupes += 1
                        file_has_dupe = True
                    issues.append(Issue(
                        check=self.NAME, severity="warning",
                        file=index.rel(abs_path), line=line_no,
                        message=f"Duplicate heading: '{title}' (level {level}) — first seen at line {seen[key]}",
                        suggestion="Merge or rename one of the duplicate sections",
                    ))
                else:
                    seen[key] = line_no

        return CheckResult(
            name=self.NAME, issues=issues,
            stats={"files_checked": len(index.all_paths), "files_with_duplicates": files_with_dupes},
        )

    def fix(self, index: VaultIndex) -> int:
        """Remove duplicate heading sections, keeping the last occurrence. Returns count of fixes."""
        fixed = 0
        for abs_path in index.all_paths:
            try:
                text = abs_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            cleaned = TextProcessor.strip_code_blocks(text)
            cleaned_lines = cleaned.split("\n")
            real_lines = text.split("\n")

            # Find heading positions in cleaned text
            heading_positions: List[Tuple[int, int, str]] = []  # (line_idx, level, title)
            for i, line in enumerate(cleaned_lines):
                m = HEADING_RE.match(line)
                if m:
                    heading_positions.append((i, len(m.group(1)), m.group(2).strip()))

            # Identify duplicates — keep last, remove earlier
            seen: Dict[Tuple[int, str], List[int]] = {}
            for idx, (line_idx, level, title) in enumerate(heading_positions):
                key = (level, title.lower())
                seen.setdefault(key, []).append(idx)

            lines_to_remove: Set[int] = set()
            for key, positions in seen.items():
                if len(positions) <= 1:
                    continue
                # Remove all but the last occurrence (remove section content up to next heading)
                for pos_idx in positions[:-1]:
                    start_line = heading_positions[pos_idx][0]
                    # Find end of section: next heading at same or higher level, or next heading position
                    end_line = len(real_lines)
                    if pos_idx + 1 < len(heading_positions):
                        end_line = heading_positions[pos_idx + 1][0]
                    for line_idx in range(start_line, end_line):
                        lines_to_remove.add(line_idx)

            if lines_to_remove:
                new_lines = [l for i, l in enumerate(real_lines) if i not in lines_to_remove]
                # Clean up consecutive blank lines
                cleaned_output: List[str] = []
                prev_blank = False
                for line in new_lines:
                    is_blank = line.strip() == ""
                    if is_blank and prev_blank:
                        continue
                    cleaned_output.append(line)
                    prev_blank = is_blank
                abs_path.write_text("\n".join(cleaned_output), encoding="utf-8")
                fixed += 1

        return fixed


class ManifestDriftChecker:
    """Check that _MANIFEST.md file count matches reality."""

    NAME = "manifest"
    FILE_COUNT_RE = re.compile(r"\*\*Files:\*\*\s*(\d+)")

    def run(self, index: VaultIndex) -> CheckResult:
        manifest_path = index.vault_root / "_MANIFEST.md"
        if not manifest_path.exists():
            return CheckResult(
                name=self.NAME,
                issues=[Issue(self.NAME, "warning", "_MANIFEST.md", 0, "Manifest not found", "Run rebuild-manifest")],
                stats={"actual_files": len(index.all_paths), "claimed_files": 0},
            )

        try:
            text = manifest_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return CheckResult(name=self.NAME, issues=[], stats={})

        m = self.FILE_COUNT_RE.search(text)
        if not m:
            return CheckResult(
                name=self.NAME,
                issues=[Issue(self.NAME, "warning", "_MANIFEST.md", 0, "No file count found in manifest", "")],
                stats={"actual_files": len(index.all_paths)},
            )

        claimed = int(m.group(1))
        actual = len(index.all_paths)
        issues: List[Issue] = []

        if claimed != actual:
            issues.append(Issue(
                check=self.NAME, severity="warning",
                file="_MANIFEST.md", line=0,
                message=f"Manifest claims {claimed} files, vault has {actual}",
                suggestion="Run rebuild-manifest.py to regenerate",
            ))

        return CheckResult(
            name=self.NAME, issues=issues,
            stats={"actual_files": actual, "claimed_files": claimed},
        )


class InboxStalenessChecker:
    """Flag inbox items older than a threshold that haven't been archived."""

    NAME = "inbox"
    STALE_DAYS = 7
    PROCESSED_RE = re.compile(r"\[processed::\s*true\s*\]", re.IGNORECASE)

    def run(self, index: VaultIndex) -> CheckResult:
        inbox_dir = index.vault_root / "inbox"
        if not inbox_dir.is_dir():
            return CheckResult(name=self.NAME, issues=[], stats={"inbox_files": 0})

        issues: List[Issue] = []
        inbox_files = 0
        now = datetime.now(timezone.utc)

        for abs_path in index.all_paths:
            rel = index.rel(abs_path)
            if not rel.startswith("inbox/"):
                continue
            inbox_files += 1

            try:
                mtime = datetime.fromtimestamp(abs_path.stat().st_mtime, tz=timezone.utc)
                age_days = (now - mtime).days
            except OSError:
                continue

            if age_days >= self.STALE_DAYS:
                try:
                    content = abs_path.read_text(encoding="utf-8", errors="replace")
                    is_processed = bool(self.PROCESSED_RE.search(content))
                except OSError:
                    is_processed = False

                severity = "info" if is_processed else "warning"
                msg = (
                    f"Processed inbox item still in inbox ({age_days} days old)" if is_processed
                    else f"Unprocessed inbox item is {age_days} days old"
                )
                issues.append(Issue(
                    check=self.NAME, severity=severity,
                    file=rel, line=0, message=msg,
                    suggestion="Move to archive/inbox/" if is_processed else "Process via ingest or archive manually",
                ))

        return CheckResult(name=self.NAME, issues=issues, stats={"inbox_files": inbox_files, "stale": len(issues)})


class EntityStubChecker:
    """Find wikilinks to entities/ that don't have a corresponding file."""

    NAME = "entity-stubs"

    def run(self, index: VaultIndex) -> CheckResult:
        missing: Dict[str, List[str]] = {}  # entity_name -> [referencing files]

        for abs_path in index.all_paths:
            try:
                text = abs_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rel = index.rel(abs_path)
            cleaned = TextProcessor.clean(text)

            for m in WIKILINK_RE.finditer(cleaned):
                target, _ = parse_wikilink(m.group(1))
                if not target.startswith("entities/"):
                    continue
                resolved = resolve_wikilink(target, index)
                if resolved is None:
                    name = target.removeprefix("entities/").removesuffix(".md")
                    missing.setdefault(name, []).append(rel)

        issues = [
            Issue(
                check=self.NAME, severity="error",
                file=f"entities/{name}.md", line=0,
                message=f"Missing entity file — referenced by: {', '.join(refs[:3])}{'...' if len(refs) > 3 else ''}",
                suggestion=f"Run: python3 scripts/create_entity_stubs.py <vault> {name}",
            )
            for name, refs in sorted(missing.items())
        ]

        return CheckResult(
            name=self.NAME, issues=issues,
            stats={"missing_entities": len(missing), "total_references": sum(len(r) for r in missing.values())},
        )


class OrphanDetector:
    """Detect files with no incoming AND no outgoing wikilinks."""

    NAME = "orphans"
    EXCLUDES = {"README.md", "CLAUDE.md", "_MANIFEST.md", ".gitignore", "entities/directory.md"}

    def run(self, index: VaultIndex) -> CheckResult:
        incoming: Dict[Path, Set[Path]] = {p: set() for p in index.all_paths}

        for abs_path in index.all_paths:
            try:
                text = abs_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            cleaned = TextProcessor.clean(text)
            for m in WIKILINK_RE.finditer(cleaned):
                target, _ = parse_wikilink(m.group(1))
                resolved = resolve_wikilink(target, index)
                if resolved:
                    incoming.setdefault(resolved, set()).add(abs_path)

        outgoing: Dict[Path, bool] = {}
        for abs_path in index.all_paths:
            try:
                text = abs_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                outgoing[abs_path] = False
                continue
            outgoing[abs_path] = bool(WIKILINK_RE.search(TextProcessor.clean(text)))

        issues: List[Issue] = []
        for abs_path in index.all_paths:
            rel = index.rel(abs_path)
            if rel in self.EXCLUDES or abs_path.name in self.EXCLUDES:
                continue
            if not incoming.get(abs_path) and not outgoing.get(abs_path, False):
                issues.append(Issue(
                    check=self.NAME, severity="warning", file=rel, line=0,
                    message="Orphan file — no incoming or outgoing wikilinks",
                    suggestion="Link from another note, or move to archive/",
                ))

        return CheckResult(
            name=self.NAME, issues=issues,
            stats={"total_files": len(index.all_paths), "orphans": len(issues)},
        )


class SyncthingConflictDetector:
    """Detect Syncthing conflict / duplicate files."""

    NAME = "conflicts"
    NUMERIC_DUP_RE = re.compile(r"^(.+)\s+(\d+)\.md$")
    DATE_FILE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")
    SYNC_CONFLICT_RE = re.compile(r"\.sync-conflict-\d{8}-\d{6}")
    CONFLICT_PAREN_RE = re.compile(r"\(conflict\)", re.IGNORECASE)

    def run(self, index: VaultIndex) -> CheckResult:
        issues: List[Issue] = []
        for abs_path in index.all_paths:
            rel = index.rel(abs_path)
            fname = abs_path.name

            if self.SYNC_CONFLICT_RE.search(fname):
                issues.append(Issue(self.NAME, "error", rel, 0, "Syncthing conflict file", ""))
            elif self.CONFLICT_PAREN_RE.search(fname):
                issues.append(Issue(self.NAME, "error", rel, 0, "Conflict file detected", ""))
            elif not self.DATE_FILE_RE.match(fname):
                m = self.NUMERIC_DUP_RE.match(fname)
                if m:
                    issues.append(Issue(
                        self.NAME, "error", rel, 0,
                        "Possible numeric duplicate", f"Original: {m.group(1)}.md",
                    ))

        return CheckResult(name=self.NAME, issues=issues, stats={"conflicts": len(issues)})


class StructureChecker:
    """Check that required directories and files exist."""

    NAME = "structure"
    REQUIRED_DIRS = ["brain", "me", "entities", "inbox", "archive", "scratch"]
    CRITICAL_FILES = [
        "brain/status.md", "brain/deadlines.md", "brain/goals.md",
        "brain/decisions.md", "brain/session-log.md",
    ]

    def run(self, index: VaultIndex) -> CheckResult:
        issues: List[Issue] = []

        for d in self.REQUIRED_DIRS:
            if not (index.vault_root / d).is_dir():
                issues.append(Issue(self.NAME, "error", d + "/", 0, f"Required directory missing: {d}/", f"mkdir -p {d}"))

        for f in self.CRITICAL_FILES:
            path = index.vault_root / f
            if not path.exists():
                issues.append(Issue(self.NAME, "error", f, 0, f"Critical file missing: {f}", "Create from template"))
            elif path.stat().st_size == 0:
                issues.append(Issue(self.NAME, "warning", f, 0, f"Critical file is empty: {f}", ""))

        return CheckResult(name=self.NAME, issues=issues, stats={
            "missing_dirs": sum(1 for i in issues if i.file.endswith("/")),
            "missing_files": sum(1 for i in issues if i.severity == "error" and not i.file.endswith("/")),
        })


class UnconvertedReferenceChecker:
    """Find plain-text mentions of entity names that should be wikilinked."""

    NAME = "suggestions"
    MIN_STEM_LEN = 3

    def run(self, index: VaultIndex) -> CheckResult:
        entities_dir = index.vault_root / "entities"
        if not entities_dir.is_dir():
            return CheckResult(name=self.NAME, issues=[], stats={})

        entity_names: Dict[str, str] = {}
        entity_paths: Set[Path] = set()
        for abs_path in index.all_paths:
            rel = index.rel(abs_path)
            if not rel.startswith("entities/"):
                continue
            entity_paths.add(abs_path)
            stem = abs_path.stem
            if len(stem) >= self.MIN_STEM_LEN:
                entity_names[stem.replace("-", " ").lower()] = rel

        if not entity_names:
            return CheckResult(name=self.NAME, issues=[], stats={})

        sorted_names = sorted(entity_names, key=len, reverse=True)
        pattern = re.compile(r"\b(" + "|".join(re.escape(n) for n in sorted_names) + r")\b", re.IGNORECASE)
        wikilink_strip = re.compile(r"\[\[[^\]]*\]\]")

        issues: List[Issue] = []
        for abs_path in index.all_paths:
            if abs_path in entity_paths:
                continue
            try:
                text = abs_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            cleaned = TextProcessor.clean(text)
            rel = index.rel(abs_path)

            for line_no, line in enumerate(cleaned.split("\n"), start=1):
                stripped = wikilink_strip.sub(lambda m: " " * len(m.group(0)), line)
                for m in pattern.finditer(stripped):
                    entity_rel = entity_names.get(m.group(1).lower(), "")
                    issues.append(Issue(
                        self.NAME, "info", rel, line_no,
                        f"Plain-text entity mention: '{m.group(1)}'",
                        f"Replace with [[{entity_rel.removesuffix('.md')}|{m.group(1)}]]" if entity_rel else "",
                    ))

        return CheckResult(name=self.NAME, issues=issues, stats={"suggestions": len(issues)})


# ---------------------------------------------------------------------------
# Reporter
# ---------------------------------------------------------------------------

class Reporter:
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    GREEN = "\033[92m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"

    def __init__(self, use_json: bool = False, quiet: bool = False):
        self.use_json = use_json
        self.quiet = quiet
        if not (sys.stdout.isatty() and "NO_COLOR" not in os.environ and not use_json):
            for attr in ("RED", "YELLOW", "BLUE", "GREEN", "BOLD", "DIM", "RESET"):
                setattr(self, attr, "")

    def report(self, results: List[CheckResult]) -> int:
        if self.use_json:
            return self._report_json(results)
        return self._report_human(results)

    def _report_json(self, results: List[CheckResult]) -> int:
        summary = {"errors": 0, "warnings": 0, "info": 0}
        checks = []
        for r in results:
            checks.append({
                "name": r.name, "stats": r.stats,
                "issues": [dataclasses.asdict(i) for i in r.issues],
            })
            for i in r.issues:
                key = i.severity + "s" if not i.severity.endswith("s") else i.severity
                summary[key] = summary.get(key, 0) + 1

        print(json.dumps({"timestamp": datetime.now(timezone.utc).isoformat(), "checks": checks, "summary": summary}, indent=2))
        return 1 if summary["errors"] + summary["warnings"] > 0 else 0

    def _report_human(self, results: List[CheckResult]) -> int:
        totals = {"error": 0, "warning": 0, "info": 0}

        for r in results:
            errors = [i for i in r.issues if i.severity == "error"]
            warnings = [i for i in r.issues if i.severity == "warning"]
            totals["error"] += len(errors)
            totals["warning"] += len(warnings)
            totals["info"] += sum(1 for i in r.issues if i.severity == "info")

            if self.quiet and not errors:
                continue

            status = f"{self.RED}FAIL" if errors else f"{self.YELLOW}WARN" if warnings else f"{self.GREEN}OK"
            print(f"\n{self.BOLD}[{r.name}]{self.RESET} {status}{self.RESET}")

            if r.stats:
                print(f"  {self.DIM}{', '.join(f'{k}: {v}' for k, v in r.stats.items())}{self.RESET}")

            for issue in (errors if self.quiet else r.issues):
                color = {"error": self.RED, "warning": self.YELLOW, "info": self.BLUE}.get(issue.severity, "")
                loc = f"{issue.file}:{issue.line}" if issue.line else issue.file
                print(f"  {color}{issue.severity.upper()}{self.RESET} {self.DIM}{loc}{self.RESET}")
                print(f"    {issue.message}")
                if issue.suggestion:
                    print(f"    {self.DIM}-> {issue.suggestion}{self.RESET}")

            if not (errors if self.quiet else r.issues):
                print(f"  {self.GREEN}No issues found.{self.RESET}")

        print(f"\n{self.BOLD}{'=' * 50}{self.RESET}")
        parts = []
        if totals["error"]:
            parts.append(f"{self.RED}{totals['error']} error(s){self.RESET}")
        if totals["warning"]:
            parts.append(f"{self.YELLOW}{totals['warning']} warning(s){self.RESET}")
        if totals["info"]:
            parts.append(f"{self.BLUE}{totals['info']} info{self.RESET}")
        print(f"  {', '.join(parts)}" if parts else f"{self.GREEN}{self.BOLD}All checks passed.{self.RESET}")

        return 1 if totals["error"] + totals["warning"] > 0 else 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

ALL_CHECKERS = {
    "wikilinks": BrokenWikilinkChecker,
    "metadata": MetadataValidator,
    "duplicates": DuplicateHeadingChecker,
    "manifest": ManifestDriftChecker,
    "inbox": InboxStalenessChecker,
    "entity-stubs": EntityStubChecker,
    "orphans": OrphanDetector,
    "conflicts": SyncthingConflictDetector,
    "structure": StructureChecker,
    "suggestions": UnconvertedReferenceChecker,
}

DEFAULT_CHECKS = {"wikilinks", "metadata", "duplicates", "manifest", "inbox", "entity-stubs", "orphans", "conflicts", "structure"}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Obsidian vault health checker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Available checks: " + ", ".join(ALL_CHECKERS),
    )
    p.add_argument("vault", help="Path to the Obsidian vault root")
    p.add_argument("--check", help="Comma-separated checks to run (default: all except suggestions)")
    p.add_argument("--json", action="store_true", dest="json_output", help="Output as JSON")
    p.add_argument("--quiet", action="store_true", help="Errors only")
    p.add_argument("--skip", help="Comma-separated directories to skip")
    p.add_argument("--modified-only", nargs="+", metavar="FILE", help="Only check these files + their link targets")
    p.add_argument("--fix", action="store_true", help="Auto-fix what's possible (duplicate headings)")
    return p


def find_vault(hint: str) -> Optional[Path]:
    """Resolve vault path with fallbacks."""
    candidates = [Path(hint).expanduser().resolve(), Path(hint).expanduser(), Path.home() / "vault"]
    env_vault = os.environ.get("VAULT_PATH")
    if env_vault:
        candidates.insert(0, Path(env_vault))
    return next((p for p in candidates if p.is_dir()), None)


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    vault_path = find_vault(args.vault)
    if vault_path is None:
        print(f"Error: vault not found: '{args.vault}'", file=sys.stderr)
        return 1

    check_names = [c.strip() for c in args.check.split(",")] if args.check else list(DEFAULT_CHECKS)
    unknown = [c for c in check_names if c not in ALL_CHECKERS]
    if unknown:
        print(f"Error: unknown check(s): {', '.join(unknown)}", file=sys.stderr)
        return 1

    extra_skip = {d.strip() for d in args.skip.split(",")} if args.skip else None

    if not args.json_output:
        print(f"Indexing vault: {vault_path} ...")

    index = VaultIndex(vault_path, extra_skip=extra_skip)

    if args.modified_only:
        index = index.scope_to(args.modified_only)

    if not args.json_output:
        print(f"Checking {len(index.all_paths)} files")

    # Auto-fix pass
    if args.fix:
        dup_checker = DuplicateHeadingChecker()
        fixed = dup_checker.fix(index)
        if fixed and not args.json_output:
            print(f"Fixed duplicate headings in {fixed} file(s)")
        # Re-index after fixes
        index = VaultIndex(vault_path, extra_skip=extra_skip)
        if args.modified_only:
            index = index.scope_to(args.modified_only)

    results = [ALL_CHECKERS[name]().run(index) for name in check_names]
    return Reporter(use_json=args.json_output, quiet=args.quiet).report(results)


if __name__ == "__main__":
    sys.exit(main())
