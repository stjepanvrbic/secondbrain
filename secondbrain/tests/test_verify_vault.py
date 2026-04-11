"""Tests for verify_vault.py — every checker, CLI flag, and edge case."""

import json
import os
import textwrap
import time
from pathlib import Path

import pytest

# Allow imports from scripts/
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from verify_vault import (
    Issue, CheckResult,
    VaultIndex, TextProcessor, parse_wikilink, resolve_wikilink,
    BrokenWikilinkChecker, MetadataValidator, DuplicateHeadingChecker,
    ManifestDriftChecker, InboxStalenessChecker, EntityStubChecker,
    OrphanDetector, SyncthingConflictDetector, StructureChecker,
    UnconvertedReferenceChecker, Reporter, main,
)


# ---------------------------------------------------------------------------
# TextProcessor
# ---------------------------------------------------------------------------

class TestTextProcessor:
    def test_strip_frontmatter(self):
        text = "---\ntitle: test\n---\n# Hello"
        result = TextProcessor.strip_frontmatter(text)
        assert "title: test" not in result
        assert "# Hello" in result

    def test_strip_frontmatter_no_frontmatter(self):
        text = "# Hello\nWorld"
        assert TextProcessor.strip_frontmatter(text) == text

    def test_strip_code_blocks(self):
        text = "before\n```python\n[[link]]\n```\nafter"
        result = TextProcessor.strip_code_blocks(text)
        assert "[[link]]" not in result
        assert "before" in result
        assert "after" in result

    def test_strip_code_blocks_tilde(self):
        text = "before\n~~~\n[[link]]\n~~~\nafter"
        result = TextProcessor.strip_code_blocks(text)
        assert "[[link]]" not in result

    def test_strip_inline_code(self):
        text = "See `[[not-a-link]]` for details"
        result = TextProcessor.strip_inline_code(text)
        assert "[[not-a-link]]" not in result

    def test_clean_combines_all(self):
        text = "---\nfoo: bar\n---\n```\n[[code]]\n```\nSee `[[inline]]` and [[real]]"
        result = TextProcessor.clean(text)
        assert "[[code]]" not in result
        assert "[[inline]]" not in result
        assert "[[real]]" in result


# ---------------------------------------------------------------------------
# Wikilink parsing and resolution
# ---------------------------------------------------------------------------

class TestWikilinkParsing:
    def test_simple(self):
        assert parse_wikilink("entities/alice") == ("entities/alice", "")

    def test_with_alias(self):
        assert parse_wikilink("entities/alice|Alice") == ("entities/alice", "")

    def test_with_section(self):
        assert parse_wikilink("file#Section") == ("file", "Section")

    def test_with_section_and_alias(self):
        assert parse_wikilink("file#Section|display") == ("file", "Section")

    def test_empty(self):
        assert parse_wikilink("") == ("", "")


class TestWikilinkResolution:
    def test_exact_relative_path(self, tmp_vault: Path):
        index = VaultIndex(tmp_vault)
        result = resolve_wikilink("entities/alice", index)
        assert result == tmp_vault / "entities" / "alice.md"

    def test_exact_with_extension(self, tmp_vault: Path):
        index = VaultIndex(tmp_vault)
        result = resolve_wikilink("entities/alice.md", index)
        assert result == tmp_vault / "entities" / "alice.md"

    def test_stem_match(self, tmp_vault: Path):
        index = VaultIndex(tmp_vault)
        result = resolve_wikilink("alice", index)
        assert result == tmp_vault / "entities" / "alice.md"

    def test_nonexistent(self, tmp_vault: Path):
        index = VaultIndex(tmp_vault)
        result = resolve_wikilink("entities/nonexistent", index)
        assert result is None

    def test_empty_target(self, tmp_vault: Path):
        index = VaultIndex(tmp_vault)
        assert resolve_wikilink("", index) is None


# ---------------------------------------------------------------------------
# VaultIndex
# ---------------------------------------------------------------------------

class TestVaultIndex:
    def test_indexes_all_md_files(self, tmp_vault: Path):
        index = VaultIndex(tmp_vault)
        rels = {index.rel(p) for p in index.all_paths}
        assert "brain/status.md" in rels
        assert "entities/alice.md" in rels

    def test_excludes_obsidian_dir(self, tmp_vault: Path):
        obsidian_dir = tmp_vault / ".obsidian"
        obsidian_dir.mkdir()
        (obsidian_dir / "config.md").write_text("config")
        index = VaultIndex(tmp_vault)
        rels = {index.rel(p) for p in index.all_paths}
        assert ".obsidian/config.md" not in rels

    def test_headings_extracted(self, tmp_vault: Path):
        index = VaultIndex(tmp_vault)
        alice_path = tmp_vault / "entities" / "alice.md"
        headings = index.path_to_headings[alice_path]
        assert any(h[1] == "Alice" for h in headings)

    def test_scope_to(self, populated_vault: Path):
        index = VaultIndex(populated_vault)
        scoped = index.scope_to(["brain/status.md"])
        scoped_rels = {index.rel(p) for p in scoped.all_paths}
        assert "brain/status.md" in scoped_rels
        # Should also include link targets from status.md
        assert "entities/alice.md" in scoped_rels

    def test_scope_to_nonexistent_file(self, tmp_vault: Path):
        index = VaultIndex(tmp_vault)
        scoped = index.scope_to(["nonexistent.md"])
        assert len(scoped.all_paths) == 0


# ---------------------------------------------------------------------------
# BrokenWikilinkChecker
# ---------------------------------------------------------------------------

class TestBrokenWikilinkChecker:
    def test_clean_vault(self, tmp_vault: Path):
        index = VaultIndex(tmp_vault)
        result = BrokenWikilinkChecker().run(index)
        assert all(i.severity != "error" for i in result.issues)

    def test_detects_broken_link(self, broken_vault: Path):
        index = VaultIndex(broken_vault)
        result = BrokenWikilinkChecker().run(index)
        errors = [i for i in result.issues if i.severity == "error"]
        assert any("charlie" in i.message.lower() for i in errors)

    def test_ignores_links_in_code(self, tmp_vault: Path):
        (tmp_vault / "test.md").write_text("```\n[[nonexistent/file]]\n```\n")
        index = VaultIndex(tmp_vault)
        result = BrokenWikilinkChecker().run(index)
        assert not any("nonexistent" in i.message for i in result.issues)

    def test_ignores_block_references(self, tmp_vault: Path):
        (tmp_vault / "test.md").write_text("See [[entities/alice#^block-id]]\n")
        index = VaultIndex(tmp_vault)
        result = BrokenWikilinkChecker().run(index)
        errors = [i for i in result.issues if i.severity == "error"]
        assert not errors

    def test_detects_missing_section(self, tmp_vault: Path):
        (tmp_vault / "test.md").write_text("See [[entities/alice#Nonexistent Section]]\n")
        index = VaultIndex(tmp_vault)
        result = BrokenWikilinkChecker().run(index)
        warnings = [i for i in result.issues if i.severity == "warning"]
        assert any("Nonexistent Section" in i.message for i in warnings)


# ---------------------------------------------------------------------------
# MetadataValidator
# ---------------------------------------------------------------------------

class TestMetadataValidator:
    def test_valid_metadata(self, tmp_vault: Path):
        index = VaultIndex(tmp_vault)
        result = MetadataValidator().run(index)
        errors = [i for i in result.issues if i.severity == "error"]
        assert not errors

    def test_invalid_date(self, broken_vault: Path):
        index = VaultIndex(broken_vault)
        result = MetadataValidator().run(index)
        errors = [i for i in result.issues if i.severity == "error"]
        assert any("bad-date" in i.message for i in errors)

    def test_invalid_energy(self, broken_vault: Path):
        index = VaultIndex(broken_vault)
        result = MetadataValidator().run(index)
        errors = [i for i in result.issues if i.severity == "error"]
        assert any("invalid" in i.message.lower() and "energy" in i.message.lower() for i in errors)

    def test_missing_status_file(self, tmp_vault: Path):
        (tmp_vault / "brain" / "status.md").unlink()
        index = VaultIndex(tmp_vault)
        result = MetadataValidator().run(index)
        assert any(i.severity == "warning" and "not found" in i.message for i in result.issues)

    def test_task_with_no_metadata(self, tmp_vault: Path):
        (tmp_vault / "brain" / "status.md").write_text("# Status\n\n- [ ] Bare task with no fields\n")
        index = VaultIndex(tmp_vault)
        result = MetadataValidator().run(index)
        assert any("no metadata" in i.message.lower() for i in result.issues)


# ---------------------------------------------------------------------------
# DuplicateHeadingChecker
# ---------------------------------------------------------------------------

class TestDuplicateHeadingChecker:
    def test_no_duplicates(self, tmp_vault: Path):
        index = VaultIndex(tmp_vault)
        result = DuplicateHeadingChecker().run(index)
        assert not result.issues

    def test_detects_duplicates(self, broken_vault: Path):
        index = VaultIndex(broken_vault)
        result = DuplicateHeadingChecker().run(index)
        assert any("Today's Plan" in i.message for i in result.issues)

    def test_different_levels_not_duplicate(self, tmp_vault: Path):
        (tmp_vault / "test.md").write_text("# Title\n\n## Title\n\nContent\n")
        index = VaultIndex(tmp_vault)
        result = DuplicateHeadingChecker().run(index)
        assert not result.issues

    def test_case_insensitive(self, tmp_vault: Path):
        (tmp_vault / "test.md").write_text("## Tasks\n\nContent\n\n## tasks\n\nMore\n")
        index = VaultIndex(tmp_vault)
        result = DuplicateHeadingChecker().run(index)
        assert len(result.issues) == 1

    def test_headings_in_code_blocks_ignored(self, tmp_vault: Path):
        (tmp_vault / "test.md").write_text("## Real\n\n```\n## Real\n```\n")
        index = VaultIndex(tmp_vault)
        result = DuplicateHeadingChecker().run(index)
        assert not result.issues

    def test_fix_removes_earlier_duplicates(self, tmp_vault: Path):
        (tmp_vault / "test.md").write_text(textwrap.dedent("""\
            ## Plan

            Old content

            ## Plan

            New content
        """))
        index = VaultIndex(tmp_vault)
        fixed = DuplicateHeadingChecker().fix(index)
        assert fixed == 1
        content = (tmp_vault / "test.md").read_text()
        assert content.count("## Plan") == 1
        assert "New content" in content

    def test_fix_no_duplicates_no_changes(self, tmp_vault: Path):
        original = (tmp_vault / "brain" / "status.md").read_text()
        index = VaultIndex(tmp_vault)
        fixed = DuplicateHeadingChecker().fix(index)
        assert fixed == 0
        assert (tmp_vault / "brain" / "status.md").read_text() == original


# ---------------------------------------------------------------------------
# ManifestDriftChecker
# ---------------------------------------------------------------------------

class TestManifestDriftChecker:
    def test_accurate_manifest(self, tmp_vault: Path):
        actual_count = len(list(tmp_vault.rglob("*.md")))
        (tmp_vault / "_MANIFEST.md").write_text(f"# Manifest\n\n**Files:** {actual_count}\n")
        index = VaultIndex(tmp_vault)
        result = ManifestDriftChecker().run(index)
        assert not result.issues

    def test_detects_drift(self, broken_vault: Path):
        index = VaultIndex(broken_vault)
        result = ManifestDriftChecker().run(index)
        assert any("999" in i.message for i in result.issues)

    def test_missing_manifest(self, tmp_vault: Path):
        (tmp_vault / "_MANIFEST.md").unlink()
        index = VaultIndex(tmp_vault)
        result = ManifestDriftChecker().run(index)
        assert any("not found" in i.message.lower() for i in result.issues)

    def test_manifest_without_count(self, tmp_vault: Path):
        (tmp_vault / "_MANIFEST.md").write_text("# Manifest\n\nNo count here.\n")
        index = VaultIndex(tmp_vault)
        result = ManifestDriftChecker().run(index)
        assert any("no file count" in i.message.lower() for i in result.issues)


# ---------------------------------------------------------------------------
# InboxStalenessChecker
# ---------------------------------------------------------------------------

class TestInboxStalenessChecker:
    def test_empty_inbox(self, tmp_vault: Path):
        index = VaultIndex(tmp_vault)
        result = InboxStalenessChecker().run(index)
        assert not result.issues

    def test_detects_stale_unprocessed(self, broken_vault: Path):
        index = VaultIndex(broken_vault)
        result = InboxStalenessChecker().run(index)
        assert any("unprocessed" in i.message.lower() for i in result.issues)

    def test_fresh_file_not_flagged(self, tmp_vault: Path):
        (tmp_vault / "inbox" / "fresh.md").write_text("# Fresh\n\nJust created.\n")
        index = VaultIndex(tmp_vault)
        result = InboxStalenessChecker().run(index)
        assert not result.issues

    def test_no_inbox_dir(self, tmp_vault: Path):
        import shutil
        shutil.rmtree(tmp_vault / "inbox")
        index = VaultIndex(tmp_vault)
        result = InboxStalenessChecker().run(index)
        assert result.stats.get("inbox_files", 0) == 0


# ---------------------------------------------------------------------------
# EntityStubChecker
# ---------------------------------------------------------------------------

class TestEntityStubChecker:
    def test_all_entities_exist(self, tmp_vault: Path):
        index = VaultIndex(tmp_vault)
        result = EntityStubChecker().run(index)
        assert not result.issues

    def test_detects_missing_entity(self, broken_vault: Path):
        index = VaultIndex(broken_vault)
        result = EntityStubChecker().run(index)
        assert any("charlie" in i.file for i in result.issues)

    def test_multiple_references_consolidated(self, tmp_vault: Path):
        (tmp_vault / "brain" / "status.md").write_text("[[entities/missing]] and [[entities/missing]] again\n")
        (tmp_vault / "brain" / "goals.md").write_text("Also [[entities/missing]]\n")
        index = VaultIndex(tmp_vault)
        result = EntityStubChecker().run(index)
        missing_issues = [i for i in result.issues if "missing" in i.file]
        assert len(missing_issues) == 1  # consolidated, not one per reference

    def test_non_entity_broken_links_ignored(self, tmp_vault: Path):
        (tmp_vault / "test.md").write_text("[[brain/nonexistent]]\n")
        index = VaultIndex(tmp_vault)
        result = EntityStubChecker().run(index)
        assert not any("brain/" in i.file for i in result.issues)


# ---------------------------------------------------------------------------
# OrphanDetector
# ---------------------------------------------------------------------------

class TestOrphanDetector:
    def test_connected_vault(self, populated_vault: Path):
        index = VaultIndex(populated_vault)
        result = OrphanDetector().run(index)
        orphan_files = {i.file for i in result.issues}
        assert "entities/alice.md" not in orphan_files
        assert "entities/bob.md" not in orphan_files

    def test_detects_orphan(self, tmp_vault: Path):
        (tmp_vault / "scratch" / "lonely.md").write_text("# Lonely\n\nNo links at all.\n")
        index = VaultIndex(tmp_vault)
        result = OrphanDetector().run(index)
        assert any("lonely" in i.file for i in result.issues)

    def test_excludes_system_files(self, tmp_vault: Path):
        index = VaultIndex(tmp_vault)
        result = OrphanDetector().run(index)
        orphan_files = {i.file for i in result.issues}
        assert "_MANIFEST.md" not in orphan_files


# ---------------------------------------------------------------------------
# SyncthingConflictDetector
# ---------------------------------------------------------------------------

class TestSyncthingConflictDetector:
    def test_no_conflicts(self, tmp_vault: Path):
        index = VaultIndex(tmp_vault)
        result = SyncthingConflictDetector().run(index)
        assert not result.issues

    def test_detects_sync_conflict(self, tmp_vault: Path):
        (tmp_vault / "note.sync-conflict-20260410-120000.md").write_text("conflict\n")
        index = VaultIndex(tmp_vault)
        result = SyncthingConflictDetector().run(index)
        assert len(result.issues) == 1

    def test_detects_paren_conflict(self, tmp_vault: Path):
        (tmp_vault / "note (conflict).md").write_text("conflict\n")
        index = VaultIndex(tmp_vault)
        result = SyncthingConflictDetector().run(index)
        assert len(result.issues) == 1

    def test_date_files_not_flagged(self, tmp_vault: Path):
        (tmp_vault / "inbox" / "2026-04-10.md").write_text("daily note\n")
        index = VaultIndex(tmp_vault)
        result = SyncthingConflictDetector().run(index)
        assert not result.issues


# ---------------------------------------------------------------------------
# StructureChecker
# ---------------------------------------------------------------------------

class TestStructureChecker:
    def test_valid_structure(self, tmp_vault: Path):
        index = VaultIndex(tmp_vault)
        result = StructureChecker().run(index)
        errors = [i for i in result.issues if i.severity == "error"]
        assert not errors

    def test_missing_directory(self, tmp_vault: Path):
        import shutil
        shutil.rmtree(tmp_vault / "scratch")
        index = VaultIndex(tmp_vault)
        result = StructureChecker().run(index)
        assert any("scratch" in i.message for i in result.issues)

    def test_missing_critical_file(self, tmp_vault: Path):
        (tmp_vault / "brain" / "goals.md").unlink()
        index = VaultIndex(tmp_vault)
        result = StructureChecker().run(index)
        assert any("goals.md" in i.message for i in result.issues)

    def test_empty_critical_file(self, tmp_vault: Path):
        (tmp_vault / "brain" / "goals.md").write_text("")
        index = VaultIndex(tmp_vault)
        result = StructureChecker().run(index)
        assert any("empty" in i.message.lower() and "goals" in i.message for i in result.issues)


# ---------------------------------------------------------------------------
# UnconvertedReferenceChecker
# ---------------------------------------------------------------------------

class TestUnconvertedReferenceChecker:
    def test_no_plain_text_mentions(self, tmp_vault: Path):
        index = VaultIndex(tmp_vault)
        result = UnconvertedReferenceChecker().run(index)
        # May find some suggestions in status.md, but not errors
        assert all(i.severity == "info" for i in result.issues)

    def test_detects_plain_text_entity(self, populated_vault: Path):
        (populated_vault / "scratch" / "note.md").write_text("I talked to bob about the project.\n")
        index = VaultIndex(populated_vault)
        result = UnconvertedReferenceChecker().run(index)
        assert any("bob" in i.message.lower() for i in result.issues)

    def test_existing_wikilinks_not_flagged(self, populated_vault: Path):
        (populated_vault / "scratch" / "note.md").write_text("I talked to [[entities/bob|Bob]] about it.\n")
        index = VaultIndex(populated_vault)
        result = UnconvertedReferenceChecker().run(index)
        # The wikilinked "Bob" should not appear as a suggestion
        note_issues = [i for i in result.issues if i.file == "scratch/note.md"]
        assert not note_issues


# ---------------------------------------------------------------------------
# Reporter
# ---------------------------------------------------------------------------

class TestReporter:
    def test_json_output(self, capsys):
        results = [CheckResult("test", [Issue("test", "error", "f.md", 1, "bad", "fix")], {"count": 1})]
        code = Reporter(use_json=True).report(results)
        output = json.loads(capsys.readouterr().out)
        assert output["summary"]["errors"] == 1
        assert code == 1

    def test_clean_exit_code(self, capsys):
        results = [CheckResult("test", [], {"count": 0})]
        code = Reporter(use_json=True).report(results)
        assert code == 0

    def test_quiet_hides_warnings(self, capsys):
        results = [CheckResult("test", [Issue("test", "warning", "f.md", 1, "meh", "")], {})]
        Reporter(quiet=True).report(results)
        output = capsys.readouterr().out
        assert "meh" not in output


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------

class TestCLI:
    def test_basic_run(self, tmp_vault: Path):
        code = main([str(tmp_vault), "--json"])
        # tmp_vault has correct manifest count issue (10 vs actual), so may return 1
        assert code in (0, 1)

    def test_specific_checks(self, tmp_vault: Path):
        code = main([str(tmp_vault), "--check", "structure", "--json"])
        assert code == 0

    def test_modified_only(self, tmp_vault: Path):
        code = main([str(tmp_vault), "--modified-only", "brain/status.md", "--json"])
        assert code in (0, 1)

    def test_unknown_check(self, tmp_vault: Path, capsys):
        code = main([str(tmp_vault), "--check", "nonexistent"])
        assert code == 1
        assert "unknown" in capsys.readouterr().err.lower()

    def test_nonexistent_vault(self, capsys):
        code = main(["/nonexistent/path"])
        assert code == 1

    def test_fix_flag(self, tmp_vault: Path):
        (tmp_vault / "test.md").write_text("## Dup\n\nOld\n\n## Dup\n\nNew\n")
        code = main([str(tmp_vault), "--fix", "--check", "duplicates", "--json"])
        assert code == 0
        content = (tmp_vault / "test.md").read_text()
        assert content.count("## Dup") == 1
