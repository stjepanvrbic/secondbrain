"""Tests for archive_inbox.py — move processed inbox files to archive."""

import os
import time
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "secondbrain" / "scripts"))
from archive_inbox import main  # type: ignore[reportMissingImports]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_processed(vault: Path, name: str, content: str = "", mtime: str | None = None) -> Path:
    body = content or f"# {name}\n\n[processed:: true]\n"
    f = vault / "inbox" / name
    f.write_text(body)
    if mtime:
        ts = time.mktime(time.strptime(mtime, "%Y-%m-%d"))
        os.utime(f, (ts, ts))
    return f


def make_unprocessed(vault: Path, name: str) -> Path:
    f = vault / "inbox" / name
    f.write_text(f"# {name}\n\nNot yet processed.\n")
    return f


# ---------------------------------------------------------------------------
# Core behavior
# ---------------------------------------------------------------------------

class TestMoveProcessed:
    def test_moves_processed_file(self, tmp_vault: Path):
        make_processed(tmp_vault, "done.md")
        code = main([str(tmp_vault)])
        assert code == 0
        assert not (tmp_vault / "inbox" / "done.md").exists()
        archived = list((tmp_vault / "archive" / "inbox").rglob("done.md"))
        assert len(archived) == 1

    def test_preserves_content(self, tmp_vault: Path):
        content = "---\ncreated: 2026-04-01\n---\n# Note\n\n[processed:: true]\n\nImportant stuff.\n"
        make_processed(tmp_vault, "keep.md", content=content)
        main([str(tmp_vault)])
        archived = next((tmp_vault / "archive" / "inbox").rglob("keep.md"))
        assert archived.read_text() == content

    def test_creates_yyyy_mm_directory(self, tmp_vault: Path):
        make_processed(tmp_vault, "dated.md", mtime="2026-03-15")
        main([str(tmp_vault)])
        assert (tmp_vault / "archive" / "inbox" / "2026-03").is_dir()
        assert (tmp_vault / "archive" / "inbox" / "2026-03" / "dated.md").exists()

    def test_multiple_processed_files(self, tmp_vault: Path):
        make_processed(tmp_vault, "a.md")
        make_processed(tmp_vault, "b.md")
        make_processed(tmp_vault, "c.md")
        code = main([str(tmp_vault)])
        assert code == 0
        archived = list((tmp_vault / "archive" / "inbox").rglob("*.md"))
        assert len(archived) == 3


# ---------------------------------------------------------------------------
# Skip behavior
# ---------------------------------------------------------------------------

class TestSkipBehavior:
    def test_skips_unprocessed(self, tmp_vault: Path):
        make_unprocessed(tmp_vault, "wip.md")
        main([str(tmp_vault)])
        assert (tmp_vault / "inbox" / "wip.md").exists()

    def test_skips_binary_with_warning(self, tmp_vault: Path, capsys):
        (tmp_vault / "inbox" / "slide.pptx").write_bytes(b"\x00\x01")
        (tmp_vault / "inbox" / "photo.png").write_bytes(b"\x89PNG")
        main([str(tmp_vault)])
        output = capsys.readouterr().out
        assert "SKIP (binary): slide.pptx" in output
        assert "SKIP (binary): photo.png" in output
        assert (tmp_vault / "inbox" / "slide.pptx").exists()


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_dry_run_no_filesystem_changes(self, tmp_vault: Path):
        make_processed(tmp_vault, "dry.md")
        before = set(tmp_vault.rglob("*"))
        main([str(tmp_vault), "--dry-run"])
        after = set(tmp_vault.rglob("*"))
        assert before == after
        assert (tmp_vault / "inbox" / "dry.md").exists()

    def test_dry_run_shows_would_move(self, tmp_vault: Path, capsys):
        make_processed(tmp_vault, "dry.md")
        main([str(tmp_vault), "--dry-run"])
        assert "WOULD MOVE" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_inbox(self, tmp_vault: Path, capsys):
        code = main([str(tmp_vault)])
        assert code == 0
        assert "0 moved" in capsys.readouterr().out

    def test_no_inbox_directory(self, tmp_vault: Path):
        import shutil
        shutil.rmtree(tmp_vault / "inbox")
        code = main([str(tmp_vault)])
        assert code == 0

    def test_nonexistent_vault(self):
        code = main(["/nonexistent/vault/path"])
        assert code == 1

    def test_duplicate_destination_gets_suffix(self, tmp_vault: Path):
        make_processed(tmp_vault, "dup.md", mtime="2026-01-15")

        dest_dir = tmp_vault / "archive" / "inbox" / "2026-01"
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / "dup.md").write_text("already here\n")

        main([str(tmp_vault)])
        assert (dest_dir / "dup.md").read_text() == "already here\n"
        assert (dest_dir / "dup-1.md").exists()

    def test_case_insensitive_processed_tag(self, tmp_vault: Path):
        make_processed(tmp_vault, "upper.md", content="# Note\n\n[Processed:: True]\n")
        main([str(tmp_vault)])
        assert not (tmp_vault / "inbox" / "upper.md").exists()


# ---------------------------------------------------------------------------
# populated_vault integration
# ---------------------------------------------------------------------------

class TestYAMLFrontmatterFormat:
    """Verify that YAML frontmatter `processed: true` is recognized."""

    def test_moves_yaml_frontmatter_processed(self, tmp_vault: Path):
        content = "---\nprocessed: true\nprocessed-date: 2026-04-12\nsource: inbox-sweep\n---\n# Note\n\nContent.\n"
        make_processed(tmp_vault, "yaml.md", content=content)
        code = main([str(tmp_vault)])
        assert code == 0
        assert not (tmp_vault / "inbox" / "yaml.md").exists()
        archived = list((tmp_vault / "archive" / "inbox").rglob("yaml.md"))
        assert len(archived) == 1

    def test_yaml_frontmatter_case_insensitive(self, tmp_vault: Path):
        content = "---\nProcessed: True\n---\n# Note\n"
        make_processed(tmp_vault, "yaml_case.md", content=content)
        main([str(tmp_vault)])
        assert not (tmp_vault / "inbox" / "yaml_case.md").exists()

    def test_ignores_processed_outside_frontmatter(self, tmp_vault: Path):
        """processed: true in body text (not frontmatter) should not match YAML format."""
        content = "# Note\n\nprocessed: true\n\nBut no frontmatter delimiters.\n"
        (tmp_vault / "inbox" / "fake_yaml.md").write_text(content)
        main([str(tmp_vault)])
        assert (tmp_vault / "inbox" / "fake_yaml.md").exists()

    def test_inline_format_still_works(self, tmp_vault: Path):
        """Existing inline [processed:: true] format must still be recognized."""
        make_processed(tmp_vault, "inline.md")
        main([str(tmp_vault)])
        assert not (tmp_vault / "inbox" / "inline.md").exists()

    def test_preserves_yaml_content_after_archive(self, tmp_vault: Path):
        content = "---\nprocessed: true\nsource: inbox-sweep\n---\n# Important\n\nData here.\n"
        make_processed(tmp_vault, "preserve.md", content=content)
        main([str(tmp_vault)])
        archived = next((tmp_vault / "archive" / "inbox").rglob("preserve.md"))
        assert archived.read_text() == content


class TestPopulatedVault:
    def test_archives_processed_keeps_unprocessed(self, populated_vault: Path):
        code = main([str(populated_vault)])
        assert code == 0
        assert not (populated_vault / "inbox" / "note-2026-04-08.md").exists()
        assert (populated_vault / "inbox" / "note-2026-04-10.md").exists()
        archived = list((populated_vault / "archive" / "inbox").rglob("note-2026-04-08.md"))
        assert len(archived) == 1


# ---------------------------------------------------------------------------
# Summary output
# ---------------------------------------------------------------------------

class TestSummary:
    def test_summary_counts(self, tmp_vault: Path, capsys):
        make_processed(tmp_vault, "go.md")
        make_unprocessed(tmp_vault, "stay.md")
        (tmp_vault / "inbox" / "pic.png").write_bytes(b"\x89PNG")

        main([str(tmp_vault)])
        out = capsys.readouterr().out
        assert "1 moved" in out
        assert "1 skipped (unprocessed)" in out
        assert "1 skipped (binary)" in out
