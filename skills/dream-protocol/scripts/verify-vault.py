#!/usr/bin/env python3
"""
verify-vault.py — Obsidian vault health checker.

Runs structural, link, metadata, and hygiene checks against an Obsidian vault.
Python 3.8+, zero external dependencies.

Usage:
    python3 verify-vault.py /path/to/vault [--check wikilinks,metadata,...] [--json] [--quiet] [--skip dir1,dir2]
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
    check: str        # e.g. "broken-wikilink", "metadata", "orphan", "conflict", "structure", "suggestion"
    severity: str     # "error", "warning", "info"
    file: str         # relative path from vault root
    line: int         # 0 if not applicable
    message: str
    suggestion: str   # "" if none


@dataclasses.dataclass
class CheckResult:
    name: str
    issues: List[Issue]
    stats: Dict[str, int]


# ---------------------------------------------------------------------------
# TextProcessor — static helpers for stripping markdown constructs
# ---------------------------------------------------------------------------

class TextProcessor:
    """Static methods for stripping frontmatter, code blocks, and inline code."""

    @staticmethod
    def strip_frontmatter(text: str) -> str:
        """Replace leading YAML frontmatter with an equal number of blank lines."""
        if not text.startswith("---"):
            return text
        lines = text.split("\n")
        if lines[0].rstrip() != "---":
            return text
        for i in range(1, len(lines)):
            if lines[i].rstrip() == "---":
                # Replace lines 0..i (inclusive) with blanks
                for j in range(0, i + 1):
                    lines[j] = ""
                return "\n".join(lines)
        return text

    @staticmethod
    def strip_code_blocks(text: str) -> str:
        """Replace fenced code blocks (``` or ~~~) with blank lines, line by line."""
        lines = text.split("\n")
        result: List[str] = []
        in_fence = False
        fence_char = ""
        fence_count = 0
        for line in lines:
            stripped = line.lstrip()
            if not in_fence:
                # Check for opening fence
                if stripped.startswith("```") or stripped.startswith("~~~"):
                    fence_char = stripped[0]
                    fence_count = 0
                    for ch in stripped:
                        if ch == fence_char:
                            fence_count += 1
                        else:
                            break
                    if fence_count >= 3:
                        in_fence = True
                        result.append("")
                        continue
                result.append(line)
            else:
                # Inside fence — look for closing
                if stripped.startswith(fence_char * fence_count):
                    candidate = stripped
                    count = 0
                    for ch in candidate:
                        if ch == fence_char:
                            count += 1
                        else:
                            break
                    # Closing fence must be at least as many chars, rest is optional
                    rest = candidate[count:].strip()
                    if count >= fence_count and rest == "":
                        in_fence = False
                        result.append("")
                        continue
                result.append("")
        return "\n".join(result)

    @staticmethod
    def strip_inline_code(text: str) -> str:
        """Replace `backtick spans` with spaces of equal length."""
        # Match single or multiple backticks used as inline code
        def _replace(m: re.Match) -> str:
            return " " * len(m.group(0))
        return re.sub(r"`+[^`\n]+`+", _replace, text)


# ---------------------------------------------------------------------------
# VaultIndex — built once, shared by all checkers
# ---------------------------------------------------------------------------

EXCLUDED_DIRS = {
    ".git", ".obsidian", ".trash", ".stfolder",
    ".tmp.driveupload", ".stversions", "node_modules",
}

HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)(?:\s+#+)?$", re.MULTILINE)


class VaultIndex:
    """Index of all .md files in the vault."""

    def __init__(self, vault_root: Path, extra_skip: Optional[List[str]] = None):
        self.vault_root = vault_root
        self.extra_skip: Set[str] = set(extra_skip) if extra_skip else set()

        # stem (lowercased) -> list of absolute paths
        self.stem_to_paths: Dict[str, List[Path]] = {}
        # absolute path -> list of heading strings
        self.path_to_headings: Dict[Path, List[str]] = {}
        # relative path from vault root (str, forward slashes) -> absolute path
        self.relative_path_to_abs: Dict[str, Path] = {}
        # all absolute paths
        self.all_paths: List[Path] = []

        self._build()

    def _should_exclude(self, dir_name: str) -> bool:
        return dir_name in EXCLUDED_DIRS or dir_name in self.extra_skip

    def _build(self) -> None:
        for dirpath, dirnames, filenames in os.walk(self.vault_root):
            # Prune excluded directories in-place
            dirnames[:] = [
                d for d in dirnames if not self._should_exclude(d)
            ]
            for fname in filenames:
                if not fname.endswith(".md"):
                    continue
                abs_path = Path(dirpath) / fname
                self.all_paths.append(abs_path)

                # Relative path (forward slashes for cross-platform wikilink matching)
                try:
                    rel = abs_path.relative_to(self.vault_root)
                except ValueError:
                    continue
                rel_str = str(rel).replace(os.sep, "/")
                self.relative_path_to_abs[rel_str] = abs_path

                # Stem index (lowercased)
                stem_lower = abs_path.stem.lower()
                self.stem_to_paths.setdefault(stem_lower, []).append(abs_path)

                # Headings
                self.path_to_headings[abs_path] = self._extract_headings(abs_path)

    @staticmethod
    def _extract_headings(path: Path) -> List[str]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        text = TextProcessor.strip_code_blocks(text)
        return HEADING_RE.findall(text)

    def rel(self, abs_path: Path) -> str:
        """Return vault-relative path as a string with forward slashes."""
        try:
            return str(abs_path.relative_to(self.vault_root)).replace(os.sep, "/")
        except ValueError:
            return str(abs_path)


# ---------------------------------------------------------------------------
# Wikilink resolver (shared helper)
# ---------------------------------------------------------------------------

WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def parse_wikilink(raw: str) -> Tuple[str, str]:
    """Parse wikilink content into (target, section). Strips alias."""
    # Remove alias: [[target|alias]] -> target
    target = raw.split("|")[0].strip()
    # Split section: target#Section -> (target, Section)
    if "#" in target:
        parts = target.split("#", 1)
        return parts[0].strip(), parts[1].strip()
    return target, ""


def resolve_wikilink(target: str, index: VaultIndex, source_path: Path) -> Optional[Path]:
    """Resolve a wikilink target to an absolute path, or None."""
    if not target:
        return None

    # 1. Exact relative path (with or without .md)
    candidates = [target, target + ".md"]
    for cand in candidates:
        if cand in index.relative_path_to_abs:
            return index.relative_path_to_abs[cand]

    # 2. Stem match (case-insensitive)
    stem_lower = target.lower()
    # Strip trailing .md for stem matching
    if stem_lower.endswith(".md"):
        stem_lower = stem_lower[:-3]
    # Also try the last component if target contains slashes
    stem_parts = stem_lower.rsplit("/", 1)
    stem_only = stem_parts[-1]

    if stem_only in index.stem_to_paths:
        matches = index.stem_to_paths[stem_only]
        if len(matches) == 1:
            return matches[0]
        # If multiple, prefer one whose relative path ends with the full target
        for m in matches:
            rel = index.rel(m)
            rel_no_ext = rel.rsplit(".", 1)[0] if "." in rel else rel
            if rel_no_ext.lower().endswith(target.lower()):
                return m
        # Just return the first
        return matches[0]

    # 3. Partial suffix match
    target_lower = target.lower()
    if not target_lower.endswith(".md"):
        target_lower += ".md"
    for rel_str, abs_path in index.relative_path_to_abs.items():
        if rel_str.lower().endswith(target_lower):
            return abs_path

    return None


# ---------------------------------------------------------------------------
# Checkers
# ---------------------------------------------------------------------------

class BrokenWikilinkChecker:
    """Check for broken wikilinks (missing target file or section)."""

    NAME = "wikilinks"
    CHECK_LABEL = "broken-wikilink"

    def run(self, index: VaultIndex) -> CheckResult:
        issues: List[Issue] = []
        files_checked = 0
        total_links = 0
        broken_files = 0
        broken_sections = 0

        for abs_path in index.all_paths:
            try:
                text = abs_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            files_checked += 1
            rel = index.rel(abs_path)

            # Strip code so we don't flag links inside code
            cleaned = TextProcessor.strip_frontmatter(text)
            cleaned = TextProcessor.strip_code_blocks(cleaned)
            cleaned = TextProcessor.strip_inline_code(cleaned)

            lines = cleaned.split("\n")
            for line_no, line in enumerate(lines, start=1):
                for m in WIKILINK_RE.finditer(line):
                    total_links += 1
                    raw = m.group(1)
                    target, section = parse_wikilink(raw)

                    # Skip block references (^block-id)
                    if section.startswith("^"):
                        continue

                    resolved = resolve_wikilink(target, index, abs_path)
                    if resolved is None:
                        # Only flag if target is non-empty
                        if target:
                            broken_files += 1
                            issues.append(Issue(
                                check=self.CHECK_LABEL,
                                severity="error",
                                file=rel,
                                line=line_no,
                                message=f"Broken wikilink: [[{raw}]] — target not found",
                                suggestion=self._suggest(target, index),
                            ))
                    elif section:
                        # Verify heading exists
                        headings = index.path_to_headings.get(resolved, [])
                        if not any(h.lower() == section.lower() for h in headings):
                            broken_sections += 1
                            issues.append(Issue(
                                check=self.CHECK_LABEL,
                                severity="warning",
                                file=rel,
                                line=line_no,
                                message=f"Missing section: [[{raw}]] — heading '{section}' not found in {index.rel(resolved)}",
                                suggestion="",
                            ))

        return CheckResult(
            name=self.CHECK_LABEL,
            issues=issues,
            stats={
                "files_checked": files_checked,
                "total_links": total_links,
                "broken_files": broken_files,
                "broken_sections": broken_sections,
            },
        )

    @staticmethod
    def _suggest(target: str, index: VaultIndex) -> str:
        stem_lower = target.lower().rsplit("/", 1)[-1]
        if stem_lower.endswith(".md"):
            stem_lower = stem_lower[:-3]
        # Simple Levenshtein-ish: find stems within edit distance
        candidates: List[str] = []
        for stem in index.stem_to_paths:
            if stem_lower in stem or stem in stem_lower:
                candidates.append(stem)
        if candidates:
            return f"Did you mean: {', '.join(candidates[:3])}?"
        return ""


class MetadataValidator:
    """Validate task metadata in brain/commitments.md."""

    NAME = "metadata"
    CHECK_LABEL = "metadata"
    TARGET = "brain/commitments.md"

    ENERGY_VALUES = {"low", "medium", "high"}
    EST_VALUES = {"5min", "10min", "15min", "30min", "1hr", "2hr"}
    TASK_RE = re.compile(r"^\s*- \[[ x]\] ")
    DUE_RE = re.compile(r"\[due::\s*(\S+)\s*\]")
    ENERGY_RE = re.compile(r"\[energy::\s*(\S+)\s*\]")
    EST_RE = re.compile(r"\[est::\s*(\S+)\s*\]")
    DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)")

    def run(self, index: VaultIndex) -> CheckResult:
        issues: List[Issue] = []
        target_path = index.vault_root / self.TARGET

        if not target_path.exists():
            return CheckResult(
                name=self.CHECK_LABEL,
                issues=[Issue(
                    check=self.CHECK_LABEL,
                    severity="warning",
                    file=self.TARGET,
                    line=0,
                    message=f"{self.TARGET} not found — skipping metadata check",
                    suggestion="",
                )],
                stats={"tasks_checked": 0, "issues_found": 0},
            )

        try:
            text = target_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return CheckResult(name=self.CHECK_LABEL, issues=[], stats={})

        lines = text.split("\n")
        current_section = "(top-level)"
        tasks_checked = 0

        for line_no, line in enumerate(lines, start=1):
            # Track section headings
            hm = self.HEADING_RE.match(line)
            if hm:
                current_section = hm.group(2).strip()
                continue

            # Only inspect task lines
            if not self.TASK_RE.match(line):
                continue

            tasks_checked += 1
            is_open = "- [ ]" in line

            # Check [due:: ...]
            due_match = self.DUE_RE.search(line)
            if due_match:
                val = due_match.group(1)
                if not self.DATE_RE.match(val):
                    issues.append(Issue(
                        check=self.CHECK_LABEL,
                        severity="error",
                        file=self.TARGET,
                        line=line_no,
                        message=f"Invalid due date '{val}' (expected YYYY-MM-DD) in section '{current_section}'",
                        suggestion="Use format: [due:: 2026-03-24]",
                    ))

            # Check [energy:: ...]
            energy_match = self.ENERGY_RE.search(line)
            if energy_match:
                val = energy_match.group(1)
                if val.lower() not in self.ENERGY_VALUES:
                    issues.append(Issue(
                        check=self.CHECK_LABEL,
                        severity="error",
                        file=self.TARGET,
                        line=line_no,
                        message=f"Invalid energy '{val}' in section '{current_section}'",
                        suggestion=f"Allowed values: {', '.join(sorted(self.ENERGY_VALUES))}",
                    ))

            # Check [est:: ...]
            est_match = self.EST_RE.search(line)
            if est_match:
                val = est_match.group(1)
                if val.lower() not in self.EST_VALUES:
                    issues.append(Issue(
                        check=self.CHECK_LABEL,
                        severity="error",
                        file=self.TARGET,
                        line=line_no,
                        message=f"Invalid estimate '{val}' in section '{current_section}'",
                        suggestion=f"Allowed values: {', '.join(sorted(self.EST_VALUES))}",
                    ))

            # Warn if open task has ZERO metadata fields
            if is_open and not due_match and not energy_match and not est_match:
                issues.append(Issue(
                    check=self.CHECK_LABEL,
                    severity="warning",
                    file=self.TARGET,
                    line=line_no,
                    message=f"Open task with no metadata fields in section '{current_section}'",
                    suggestion="Add at least [due:: ...], [energy:: ...], or [est:: ...]",
                ))

        return CheckResult(
            name=self.CHECK_LABEL,
            issues=issues,
            stats={"tasks_checked": tasks_checked, "issues_found": len(issues)},
        )


class OrphanDetector:
    """Detect files with no incoming AND no outgoing wikilinks."""

    NAME = "orphans"
    CHECK_LABEL = "orphan"
    EXCLUDES = {
        "README.md", "CLAUDE.md", "_MANIFEST.md", ".gitignore",
        "entities/directory.md",
    }

    def run(self, index: VaultIndex) -> CheckResult:
        issues: List[Issue] = []

        # Build outgoing and incoming link maps
        outgoing: Dict[Path, Set[Path]] = {}
        incoming: Dict[Path, Set[Path]] = {}

        for abs_path in index.all_paths:
            incoming.setdefault(abs_path, set())
            outgoing.setdefault(abs_path, set())

        for abs_path in index.all_paths:
            try:
                text = abs_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            cleaned = TextProcessor.strip_frontmatter(text)
            cleaned = TextProcessor.strip_code_blocks(cleaned)
            cleaned = TextProcessor.strip_inline_code(cleaned)

            for m in WIKILINK_RE.finditer(cleaned):
                raw = m.group(1)
                target, _ = parse_wikilink(raw)
                resolved = resolve_wikilink(target, index, abs_path)
                if resolved is not None:
                    outgoing[abs_path].add(resolved)
                    incoming.setdefault(resolved, set()).add(abs_path)

        # Find orphans: no incoming AND no outgoing
        orphan_count = 0
        for abs_path in index.all_paths:
            rel = index.rel(abs_path)
            if rel in self.EXCLUDES or abs_path.name in self.EXCLUDES:
                continue
            has_incoming = len(incoming.get(abs_path, set())) > 0
            has_outgoing = len(outgoing.get(abs_path, set())) > 0
            if not has_incoming and not has_outgoing:
                orphan_count += 1
                issues.append(Issue(
                    check=self.CHECK_LABEL,
                    severity="warning",
                    file=rel,
                    line=0,
                    message="Orphan file — no incoming or outgoing wikilinks",
                    suggestion="Link this file from another note, or move to archive/",
                ))

        return CheckResult(
            name=self.CHECK_LABEL,
            issues=issues,
            stats={"total_files": len(index.all_paths), "orphans": orphan_count},
        )


class SyncthingConflictDetector:
    """Detect Syncthing conflict / duplicate files."""

    NAME = "conflicts"
    CHECK_LABEL = "conflict"

    # Pattern 1: "filename N.md" where N is a digit — but NOT date files like 2026-03-05.md
    NUMERIC_DUP_RE = re.compile(r"^(.+)\s+(\d+)\.md$")
    DATE_FILE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")

    # Pattern 2: .sync-conflict-YYYYMMDD-HHMMSS
    SYNC_CONFLICT_RE = re.compile(r"\.sync-conflict-\d{8}-\d{6}")

    # Pattern 3: (conflict) in name
    CONFLICT_PAREN_RE = re.compile(r"\(conflict\)", re.IGNORECASE)

    def run(self, index: VaultIndex) -> CheckResult:
        issues: List[Issue] = []

        for abs_path in index.all_paths:
            rel = index.rel(abs_path)
            fname = abs_path.name

            # Pattern 2: sync-conflict timestamp
            if self.SYNC_CONFLICT_RE.search(fname):
                original = self.SYNC_CONFLICT_RE.sub("", fname)
                issues.append(Issue(
                    check=self.CHECK_LABEL,
                    severity="error",
                    file=rel,
                    line=0,
                    message=f"Syncthing conflict file detected",
                    suggestion=f"Likely original: {original}",
                ))
                continue

            # Pattern 3: (conflict) in name
            if self.CONFLICT_PAREN_RE.search(fname):
                original = self.CONFLICT_PAREN_RE.sub("", fname).strip()
                issues.append(Issue(
                    check=self.CHECK_LABEL,
                    severity="error",
                    file=rel,
                    line=0,
                    message=f"Conflict file detected",
                    suggestion=f"Likely original: {original}",
                ))
                continue

            # Pattern 1: numeric duplicate (skip date files)
            if not self.DATE_FILE_RE.match(fname):
                m = self.NUMERIC_DUP_RE.match(fname)
                if m:
                    original_stem = m.group(1)
                    original_name = original_stem + ".md"
                    issues.append(Issue(
                        check=self.CHECK_LABEL,
                        severity="error",
                        file=rel,
                        line=0,
                        message=f"Possible Syncthing numeric duplicate",
                        suggestion=f"Likely original: {original_name}",
                    ))

        return CheckResult(
            name=self.CHECK_LABEL,
            issues=issues,
            stats={"files_scanned": len(index.all_paths), "conflicts": len(issues)},
        )


class StructureChecker:
    """Check that required directories and files exist and are non-empty."""

    NAME = "structure"
    CHECK_LABEL = "structure"

    REQUIRED_DIRS = ["brain", "me", "entities", "inbox", "archive", "scratch"]
    CRITICAL_FILES = [
        "brain/status.md",
        "brain/commitments.md",
        "brain/deadlines.md",
        "brain/goals.md",
        "brain/decisions.md",
        "brain/session-log.md",
    ]

    def run(self, index: VaultIndex) -> CheckResult:
        issues: List[Issue] = []

        # Check directories
        for d in self.REQUIRED_DIRS:
            dir_path = index.vault_root / d
            if not dir_path.is_dir():
                issues.append(Issue(
                    check=self.CHECK_LABEL,
                    severity="error",
                    file=d + "/",
                    line=0,
                    message=f"Required directory missing: {d}/",
                    suggestion=f"Create directory: mkdir -p {d}",
                ))

        # Check critical files
        for f in self.CRITICAL_FILES:
            file_path = index.vault_root / f
            if not file_path.exists():
                issues.append(Issue(
                    check=self.CHECK_LABEL,
                    severity="error",
                    file=f,
                    line=0,
                    message=f"Critical file missing: {f}",
                    suggestion=f"Create file with appropriate template",
                ))
            elif file_path.stat().st_size == 0:
                issues.append(Issue(
                    check=self.CHECK_LABEL,
                    severity="warning",
                    file=f,
                    line=0,
                    message=f"Critical file is empty: {f}",
                    suggestion="Add content to this file",
                ))

        missing_dirs = sum(1 for i in issues if i.severity == "error" and i.file.endswith("/"))
        missing_files = sum(1 for i in issues if i.severity == "error" and not i.file.endswith("/"))
        empty_files = sum(1 for i in issues if i.severity == "warning")

        return CheckResult(
            name=self.CHECK_LABEL,
            issues=issues,
            stats={
                "required_dirs": len(self.REQUIRED_DIRS),
                "missing_dirs": missing_dirs,
                "critical_files": len(self.CRITICAL_FILES),
                "missing_files": missing_files,
                "empty_files": empty_files,
            },
        )


class UnconvertedReferenceChecker:
    """Find plain-text mentions of entity names that are not wikilinked."""

    NAME = "suggestions"
    CHECK_LABEL = "suggestion"
    MIN_STEM_LEN = 3

    def run(self, index: VaultIndex) -> CheckResult:
        issues: List[Issue] = []

        # Build entity name list from entities/ directory
        entities_dir = index.vault_root / "entities"
        if not entities_dir.is_dir():
            return CheckResult(
                name=self.CHECK_LABEL,
                issues=[],
                stats={"entities": 0, "files_checked": 0, "suggestions": 0},
            )

        # Map: display name (lowered) -> relative path of entity file
        entity_names: Dict[str, str] = {}
        entity_paths: Set[Path] = set()
        for abs_path in index.all_paths:
            rel = index.rel(abs_path)
            if not rel.startswith("entities/"):
                continue
            entity_paths.add(abs_path)
            stem = abs_path.stem
            if len(stem) < self.MIN_STEM_LEN:
                continue
            # kebab-to-space conversion for display name
            display = stem.replace("-", " ")
            entity_names[display.lower()] = rel

        if not entity_names:
            return CheckResult(
                name=self.CHECK_LABEL,
                issues=[],
                stats={"entities": 0, "files_checked": 0, "suggestions": 0},
            )

        # Build combined regex for all entity names (case-insensitive, word boundary)
        # Sort by length descending so longer names match first
        sorted_names = sorted(entity_names.keys(), key=len, reverse=True)
        escaped = [re.escape(name) for name in sorted_names]
        combined_pattern = re.compile(
            r"\b(" + "|".join(escaped) + r")\b",
            re.IGNORECASE,
        )

        # Regex to remove existing wikilinks from text (replace with spaces)
        wikilink_strip_re = re.compile(r"\[\[[^\]]*\]\]")

        files_checked = 0
        for abs_path in index.all_paths:
            # Skip entity files themselves
            if abs_path in entity_paths:
                continue

            try:
                text = abs_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            files_checked += 1
            rel = index.rel(abs_path)

            cleaned = TextProcessor.strip_frontmatter(text)
            cleaned = TextProcessor.strip_code_blocks(cleaned)
            cleaned = TextProcessor.strip_inline_code(cleaned)

            lines = cleaned.split("\n")
            for line_no, line in enumerate(lines, start=1):
                # Strip existing wikilinks from the line
                line_clean = wikilink_strip_re.sub(lambda m: " " * len(m.group(0)), line)
                for m in combined_pattern.finditer(line_clean):
                    matched_name = m.group(1).lower()
                    entity_rel = entity_names.get(matched_name, "")
                    issues.append(Issue(
                        check=self.CHECK_LABEL,
                        severity="info",
                        file=rel,
                        line=line_no,
                        message=f"Plain-text mention of entity '{m.group(1)}' could be a wikilink",
                        suggestion=f"Replace with [[{entity_rel.replace('.md', '')}|{m.group(1)}]]" if entity_rel else "",
                    ))

        return CheckResult(
            name=self.CHECK_LABEL,
            issues=issues,
            stats={
                "entities": len(entity_names),
                "files_checked": files_checked,
                "suggestions": len(issues),
            },
        )


# ---------------------------------------------------------------------------
# Reporter
# ---------------------------------------------------------------------------

class Reporter:
    """Format and display check results."""

    # ANSI color codes
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
        # Disable colors if not a TTY or NO_COLOR is set
        self.color = (
            sys.stdout.isatty()
            and "NO_COLOR" not in os.environ
            and not use_json
        )
        if not self.color:
            self.RED = ""
            self.YELLOW = ""
            self.BLUE = ""
            self.GREEN = ""
            self.BOLD = ""
            self.DIM = ""
            self.RESET = ""

    def report(self, results: List[CheckResult]) -> int:
        """Print results and return exit code (0=clean, 1=issues)."""
        if self.use_json:
            return self._report_json(results)
        return self._report_human(results)

    def _report_json(self, results: List[CheckResult]) -> int:
        output = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "checks": [],
            "summary": {"errors": 0, "warnings": 0, "info": 0},
        }
        for r in results:
            check_data = {
                "name": r.name,
                "stats": r.stats,
                "issues": [
                    {
                        "check": i.check,
                        "severity": i.severity,
                        "file": i.file,
                        "line": i.line,
                        "message": i.message,
                        "suggestion": i.suggestion,
                    }
                    for i in r.issues
                ],
            }
            output["checks"].append(check_data)
            for i in r.issues:
                if i.severity in output["summary"]:
                    output["summary"][i.severity] += 1

        print(json.dumps(output, indent=2))
        total_actionable = output["summary"]["errors"] + output["summary"]["warnings"]
        return 1 if total_actionable > 0 else 0

    def _report_human(self, results: List[CheckResult]) -> int:
        total_errors = 0
        total_warnings = 0
        total_info = 0

        for r in results:
            errors = [i for i in r.issues if i.severity == "error"]
            warnings = [i for i in r.issues if i.severity == "warning"]
            infos = [i for i in r.issues if i.severity == "info"]
            total_errors += len(errors)
            total_warnings += len(warnings)
            total_info += len(infos)

            if self.quiet and not errors:
                continue

            # Section header
            status_icon = self._status_icon(errors, warnings)
            print(f"\n{self.BOLD}[{r.name}]{self.RESET} {status_icon}")

            # Stats line
            if r.stats:
                stats_str = ", ".join(f"{k}: {v}" for k, v in r.stats.items())
                print(f"  {self.DIM}{stats_str}{self.RESET}")

            # Issues
            issues_to_show = errors if self.quiet else r.issues
            for issue in issues_to_show:
                self._print_issue(issue)

            if not issues_to_show:
                print(f"  {self.GREEN}No issues found.{self.RESET}")

        # Summary
        print(f"\n{self.BOLD}{'=' * 50}{self.RESET}")
        parts = []
        if total_errors:
            parts.append(f"{self.RED}{total_errors} error(s){self.RESET}")
        if total_warnings:
            parts.append(f"{self.YELLOW}{total_warnings} warning(s){self.RESET}")
        if total_info:
            parts.append(f"{self.BLUE}{total_info} info{self.RESET}")
        if not parts:
            print(f"{self.GREEN}{self.BOLD}All checks passed.{self.RESET}")
        else:
            print(f"  {', '.join(parts)}")

        return 1 if (total_errors + total_warnings) > 0 else 0

    def _status_icon(self, errors: list, warnings: list) -> str:
        if errors:
            return f"{self.RED}FAIL{self.RESET}"
        if warnings:
            return f"{self.YELLOW}WARN{self.RESET}"
        return f"{self.GREEN}OK{self.RESET}"

    def _print_issue(self, issue: Issue) -> None:
        color = {
            "error": self.RED,
            "warning": self.YELLOW,
            "info": self.BLUE,
        }.get(issue.severity, "")

        loc = issue.file
        if issue.line > 0:
            loc += f":{issue.line}"

        severity_tag = f"{color}{issue.severity.upper()}{self.RESET}"
        print(f"  {severity_tag} {self.DIM}{loc}{self.RESET}")
        print(f"    {issue.message}")
        if issue.suggestion:
            print(f"    {self.DIM}-> {issue.suggestion}{self.RESET}")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

ALL_CHECKERS = {
    "wikilinks": BrokenWikilinkChecker,
    "metadata": MetadataValidator,
    "orphans": OrphanDetector,
    "conflicts": SyncthingConflictDetector,
    "structure": StructureChecker,
    "suggestions": UnconvertedReferenceChecker,
}

DEFAULT_CHECKS = {"wikilinks", "metadata", "orphans", "conflicts", "structure"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Obsidian vault health checker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Available checks:\n"
            "  wikilinks    Broken wikilink detection\n"
            "  metadata     Task metadata validation in brain/commitments.md\n"
            "  orphans      Files with no incoming or outgoing links\n"
            "  conflicts    Syncthing conflict / duplicate files\n"
            "  structure    Required directories and critical files\n"
            "  suggestions  Plain-text entity mentions (opt-in)\n"
            "\n"
            "By default all checks EXCEPT 'suggestions' are run."
        ),
    )
    parser.add_argument(
        "vault",
        type=str,
        help="Path to the Obsidian vault root",
    )
    parser.add_argument(
        "--check",
        type=str,
        default=None,
        help="Comma-separated list of checks to run (default: all except suggestions)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output results as JSON",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Show errors only (suppress warnings and info)",
    )
    parser.add_argument(
        "--skip",
        type=str,
        default=None,
        help="Comma-separated list of additional directories to skip",
    )
    return parser


def _find_vault(hint: str) -> Optional[Path]:
    """Resolve vault path, handling sandboxed scheduled-task environments.

    In Cowork scheduled tasks the home dir may be /sessions/<id>/ and
    the default vault location may not exist.  Try the given hint first,
    then fall back to VAULT_PATH env var, then common defaults.
    """
    candidates = [
        Path(hint).expanduser().resolve(),
        Path(hint).expanduser(),           # without resolve (avoids broken symlinks)
        Path.home() / "vault",             # plugin default vault location
    ]
    # Also check VAULT_PATH env var if set (highest priority)
    env_vault = os.environ.get("VAULT_PATH")
    if env_vault:
        candidates.insert(0, Path(env_vault))

    for p in candidates:
        if p.is_dir():
            return p
    return None


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    vault_path = _find_vault(args.vault)
    if vault_path is None:
        print(f"Error: could not find vault directory (tried '{args.vault}' and common fallbacks)", file=sys.stderr)
        print(f"Hint: set VAULT_PATH env var or pass an absolute path", file=sys.stderr)
        return 1

    # Determine which checks to run
    if args.check:
        check_names = [c.strip() for c in args.check.split(",")]
        unknown = [c for c in check_names if c not in ALL_CHECKERS]
        if unknown:
            print(f"Error: unknown check(s): {', '.join(unknown)}", file=sys.stderr)
            print(f"Available: {', '.join(ALL_CHECKERS.keys())}", file=sys.stderr)
            return 1
    else:
        check_names = list(DEFAULT_CHECKS)

    extra_skip = [d.strip() for d in args.skip.split(",")] if args.skip else None

    # Build index
    reporter = Reporter(use_json=args.json_output, quiet=args.quiet)

    if not args.json_output:
        print(f"Indexing vault: {vault_path} ...")

    index = VaultIndex(vault_path, extra_skip=extra_skip)

    if not args.json_output:
        print(f"Found {len(index.all_paths)} .md files")

    # Run checks
    results: List[CheckResult] = []
    for name in check_names:
        checker_cls = ALL_CHECKERS[name]
        checker = checker_cls()
        result = checker.run(index)
        results.append(result)

    return reporter.report(results)


if __name__ == "__main__":
    sys.exit(main())
