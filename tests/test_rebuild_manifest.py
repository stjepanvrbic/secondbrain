"""Tests for rebuild_manifest.py — manifest generation, parsing, and edge cases."""

import textwrap
from datetime import datetime, timedelta
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "secondbrain" / "scripts"))

from rebuild_manifest import (  # type: ignore[reportMissingImports]
    collect_md_files,
    count_per_directory,
    list_entities,
    list_domains,
    parse_recent_log_entries,
    build_manifest,
    write_manifest_atomic,
    main,
)


# ---------------------------------------------------------------------------
# File counting
# ---------------------------------------------------------------------------

class TestFileCount:
    def test_total_count_minimal(self, tmp_vault):
        files = collect_md_files(tmp_vault)
        names = {f.name for f in files}
        assert "status.md" in names
        assert "log.md" in names
        assert len(files) >= 8

    def test_total_count_populated(self, populated_vault):
        files = collect_md_files(populated_vault)
        assert len(files) >= 10

    def test_excludes_dotfiles_and_system(self, tmp_vault):
        hidden = tmp_vault / ".obsidian"
        hidden.mkdir(exist_ok=True)
        (hidden / "config.md").write_text("# config")

        git = tmp_vault / ".git"
        git.mkdir(exist_ok=True)
        (git / "info.md").write_text("# git")

        nm = tmp_vault / "node_modules"
        nm.mkdir(exist_ok=True)
        (nm / "pkg.md").write_text("# pkg")

        files = collect_md_files(tmp_vault)
        paths = {str(f) for f in files}
        assert not any(".obsidian" in p for p in paths)
        assert not any(".git" in p for p in paths)
        assert not any("node_modules" in p for p in paths)


# ---------------------------------------------------------------------------
# Per-directory counts
# ---------------------------------------------------------------------------

class TestDirectoryCounts:
    def test_counts_per_dir(self, tmp_vault):
        files = collect_md_files(tmp_vault)
        counts = count_per_directory(tmp_vault, files)
        assert counts.get("brain", 0) >= 4
        assert counts.get("entities", 0) >= 1
        assert counts.get("me", 0) >= 1

    def test_counts_populated(self, populated_vault):
        files = collect_md_files(populated_vault)
        counts = count_per_directory(populated_vault, files)
        assert counts["entities"] >= 3
        assert counts["inbox"] >= 2

    def test_empty_directory_not_listed(self, tmp_vault):
        (tmp_vault / "emptydir").mkdir()
        files = collect_md_files(tmp_vault)
        counts = count_per_directory(tmp_vault, files)
        assert "emptydir" not in counts


# ---------------------------------------------------------------------------
# Entity listing
# ---------------------------------------------------------------------------

class TestEntities:
    def test_lists_all_entities(self, populated_vault):
        entities = list_entities(populated_vault)
        paths = [path for path, _ in entities]
        assert "entities/alice" in paths
        assert "entities/bob" in paths
        assert "entities/acme-corp" in paths

    def test_wikilink_format(self, populated_vault):
        entities = list_entities(populated_vault)
        for path, display in entities:
            assert path.startswith("entities/")
            assert "|" not in path
            assert len(display) > 0

    def test_display_name_formatting(self, populated_vault):
        entities = list_entities(populated_vault)
        displays = {path: display for path, display in entities}
        assert displays["entities/acme-corp"] == "Acme Corp"
        assert displays["entities/alice"] == "Alice"

    def test_no_entities_dir(self, tmp_path):
        result = list_entities(tmp_path)
        assert result == []

    def test_empty_entities_dir(self, tmp_vault):
        for f in (tmp_vault / "entities").iterdir():
            f.unlink()
        result = list_entities(tmp_vault)
        assert result == []


# ---------------------------------------------------------------------------
# Domain folders
# ---------------------------------------------------------------------------

class TestDomains:
    def test_identifies_domain_folders(self, tmp_vault):
        (tmp_vault / "work").mkdir()
        (tmp_vault / "health").mkdir()
        domains = list_domains(tmp_vault)
        assert "work" in domains
        assert "health" in domains

    def test_excludes_system_dirs(self, tmp_vault):
        domains = list_domains(tmp_vault)
        for sdir in ["brain", "entities", "me", "inbox", "archive", "scratch"]:
            assert sdir not in domains

    def test_excludes_hidden_and_special(self, tmp_vault):
        (tmp_vault / ".hidden").mkdir()
        (tmp_vault / "_private").mkdir()
        domains = list_domains(tmp_vault)
        assert ".hidden" not in domains
        assert "_private" not in domains

    def test_no_domain_folders(self, tmp_vault):
        domains = list_domains(tmp_vault)
        assert domains == []


# ---------------------------------------------------------------------------
# Log parsing
# ---------------------------------------------------------------------------

class TestLogParsing:
    def test_parses_recent_entries(self, tmp_vault):
        today = datetime.now().strftime("%Y-%m-%d")
        (tmp_vault / "log.md").write_text(textwrap.dedent(f"""\
            # Log

            ## [{today} 14:00] create | New entity
            Created alice.

            ## [{today} 10:00] session-start | Morning session
            Loaded context.
        """))
        entries = parse_recent_log_entries(tmp_vault)
        assert len(entries) == 2
        assert entries[0][1] == "create"
        assert entries[1][1] == "session-start"

    def test_filters_old_entries(self, tmp_vault):
        old_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        today = datetime.now().strftime("%Y-%m-%d")
        (tmp_vault / "log.md").write_text(textwrap.dedent(f"""\
            # Log

            ## [{today} 10:00] session-start | Today
            Recent.

            ## [{old_date} 10:00] session-start | Old session
            Ancient history.
        """))
        entries = parse_recent_log_entries(tmp_vault)
        assert len(entries) == 1
        assert entries[0][2] == "Today"

    def test_missing_log_file(self, tmp_vault):
        (tmp_vault / "log.md").unlink()
        entries = parse_recent_log_entries(tmp_vault)
        assert entries == []

    def test_empty_log_file(self, tmp_vault):
        (tmp_vault / "log.md").write_text("# Log\n")
        entries = parse_recent_log_entries(tmp_vault)
        assert entries == []

    def test_malformed_entries_skipped(self, tmp_vault):
        today = datetime.now().strftime("%Y-%m-%d")
        (tmp_vault / "log.md").write_text(textwrap.dedent(f"""\
            # Log

            ## [{today} 10:00] session-start | Good entry
            Content.

            ## Not a valid entry
            Random text.

            ## [bad-date 10:00] op | Bad date
            Invalid.
        """))
        entries = parse_recent_log_entries(tmp_vault)
        assert len(entries) == 1
        assert entries[0][2] == "Good entry"


# ---------------------------------------------------------------------------
# Full manifest output
# ---------------------------------------------------------------------------

class TestBuildManifest:
    def test_contains_file_count(self, populated_vault):
        content = build_manifest(populated_vault)
        assert "**Files:**" in content

    def test_contains_structure_table(self, populated_vault):
        content = build_manifest(populated_vault)
        assert "## Structure" in content
        assert "| brain/" in content
        assert "| entities/" in content

    def test_contains_entities_section(self, populated_vault):
        content = build_manifest(populated_vault)
        assert "## Entities" in content
        assert "[[entities/alice|Alice]]" in content
        assert "[[entities/bob|Bob]]" in content

    def test_contains_domains_section(self, tmp_vault):
        (tmp_vault / "work").mkdir()
        content = build_manifest(tmp_vault)
        assert "## Domains" in content
        assert "- work/" in content

    def test_no_entities_shows_message(self, tmp_vault):
        for f in (tmp_vault / "entities").iterdir():
            f.unlink()
        content = build_manifest(tmp_vault)
        assert "No entities found." in content

    def test_no_domains_shows_message(self, tmp_vault):
        content = build_manifest(tmp_vault)
        assert "No domain folders found." in content

    def test_recent_activity_section(self, tmp_vault):
        today = datetime.now().strftime("%Y-%m-%d")
        (tmp_vault / "log.md").write_text(
            f"# Log\n\n## [{today} 09:00] deploy | Ship v2\nDone.\n"
        )
        content = build_manifest(tmp_vault)
        assert "## Recent Activity (Last 7 Days)" in content
        assert f"[{today}] deploy: Ship v2" in content

    def test_no_recent_activity(self, tmp_vault):
        (tmp_vault / "log.md").unlink()
        content = build_manifest(tmp_vault)
        assert "No recent activity." in content


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------

class TestAtomicWrite:
    def test_writes_manifest_file(self, tmp_vault):
        write_manifest_atomic(tmp_vault, "# Test Manifest\n")
        result = (tmp_vault / "_MANIFEST.md").read_text()
        assert result == "# Test Manifest\n"

    def test_no_tmp_file_remains(self, tmp_vault):
        write_manifest_atomic(tmp_vault, "# Test\n")
        assert not (tmp_vault / "_MANIFEST.md.tmp").exists()

    def test_overwrites_existing(self, tmp_vault):
        old_content = (tmp_vault / "_MANIFEST.md").read_text()
        assert "999" not in old_content
        write_manifest_atomic(tmp_vault, "# New content\n")
        assert (tmp_vault / "_MANIFEST.md").read_text() == "# New content\n"


# ---------------------------------------------------------------------------
# CLI (main)
# ---------------------------------------------------------------------------

class TestMain:
    def test_success_exit_code(self, tmp_vault):
        assert main([str(tmp_vault)]) == 0

    def test_manifest_written(self, tmp_vault):
        main([str(tmp_vault)])
        content = (tmp_vault / "_MANIFEST.md").read_text()
        assert content.startswith("# Vault Manifest")

    def test_invalid_path_returns_1(self):
        assert main(["/nonexistent/path/to/vault"]) == 1

    def test_no_args_returns_1(self):
        assert main([]) == 1

    def test_too_many_args_returns_1(self):
        assert main(["/a", "/b"]) == 1
