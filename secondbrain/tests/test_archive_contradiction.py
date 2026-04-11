"""Tests for archive_contradiction.py — soft-archive contradicted vault content."""

import json
import sys
import textwrap
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from archive_contradiction import extract_section, main, slugify  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def current_month() -> str:
    return datetime.now().strftime("%Y-%m")


def write_original(vault: Path, name: str, body: str) -> Path:
    f = vault / "brain" / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(body)
    return f


def write_new_content(tmp_path: Path, body: str, name: str = "new.md") -> Path:
    f = tmp_path / name
    f.write_text(body)
    return f


def run_script(
    vault: Path,
    original: Path,
    new_content: Path,
    *,
    subject: str = "acme-renewal-date",
    section_anchor: str | None = None,
    source: str = "2026-04-10 session log from Alice",
    reasoning: str = "Direct from account owner supersedes stale note",
    dry_run: bool = False,
) -> int:
    args = [
        str(vault),
        "--original-file",
        str(original),
        "--new-content-file",
        str(new_content),
        "--source-description",
        source,
        "--reasoning",
        reasoning,
        "--subject",
        subject,
    ]
    if section_anchor:
        args += ["--section-anchor", section_anchor]
    if dry_run:
        args += ["--dry-run"]
    return main(args)


# ---------------------------------------------------------------------------
# slugify / extract_section unit tests
# ---------------------------------------------------------------------------


class TestSlugify:
    def test_basic_kebab(self):
        assert slugify("Acme Renewal Date") == "acme-renewal-date"

    def test_strips_punctuation(self):
        assert slugify("Acme/Renewal: Date!") == "acme-renewal-date"

    def test_collapses_whitespace(self):
        assert slugify("  acme    renewal   ") == "acme-renewal"

    def test_trims_to_60_chars(self):
        long = "a" * 100
        result = slugify(long)
        assert len(result) <= 60
        assert result == "a" * 60

    def test_empty_becomes_untitled(self):
        assert slugify("") == "untitled"
        assert slugify("   !!!  ") == "untitled"

    def test_lowercase(self):
        assert slugify("ACME") == "acme"


class TestExtractSection:
    def test_extracts_matching_section(self):
        content = "# Top\n\n## Alpha\n\nA text\n\n## Beta\n\nB text\n"
        assert extract_section(content, "Alpha") == "## Alpha\n\nA text\n"

    def test_stops_at_same_level_heading(self):
        content = "## A\n\ntext A\n\n## B\n\ntext B\n"
        assert extract_section(content, "A") == "## A\n\ntext A\n"

    def test_includes_subsections(self):
        content = "## A\n\ntext A\n\n### A sub\n\nsub text\n\n## B\n\ntext B\n"
        got = extract_section(content, "A")
        assert "### A sub" in got
        assert "sub text" in got
        assert "text B" not in got

    def test_missing_returns_none(self):
        assert extract_section("# Top\n\n## A\n\ntext\n", "Nope") is None

    def test_case_insensitive_match(self):
        content = "## Acme Renewal\n\ntext\n"
        assert extract_section(content, "acme renewal") is not None


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_creates_archive_and_sidecar(
        self, tmp_vault: Path, tmp_path: Path, capsys
    ):
        original = write_original(tmp_vault, "note.md", "# Note\n\nOld fact.\n")
        new = write_new_content(tmp_path, "New fact replaces old.\n")

        code = run_script(tmp_vault, original, new, subject="old-fact")
        assert code == 0

        archive_dir = tmp_vault / "archive" / "contradictions" / current_month()
        assert (archive_dir / "old-fact.md").exists()
        assert (archive_dir / "old-fact.sidecar.md").exists()

    def test_archive_contains_original_content(
        self, tmp_vault: Path, tmp_path: Path
    ):
        body = "# Old Note\n\nThis is the superseded content.\n"
        original = write_original(tmp_vault, "note.md", body)
        new = write_new_content(tmp_path, "new\n")

        run_script(tmp_vault, original, new, subject="note")

        archived = (
            tmp_vault / "archive" / "contradictions" / current_month() / "note.md"
        )
        assert archived.read_text() == body

    def test_sidecar_has_all_four_fields(self, tmp_vault: Path, tmp_path: Path):
        original = write_original(tmp_vault, "note.md", "Old fact.\n")
        new = write_new_content(tmp_path, "New fact.\n")

        run_script(
            tmp_vault,
            original,
            new,
            subject="fact-update",
            source="2026-04-10 email from Bob",
            reasoning="Direct primary source beats the hearsay note",
        )

        sidecar = (
            tmp_vault
            / "archive"
            / "contradictions"
            / current_month()
            / "fact-update.sidecar.md"
        ).read_text()

        assert "type: contradiction-sidecar" in sidecar
        assert "original-path: brain/note.md" in sidecar
        assert "subject: fact-update" in sidecar
        assert "## Superseded content" in sidecar
        assert "Old fact." in sidecar
        assert "## New content" in sidecar
        assert "New fact." in sidecar
        assert "## Source" in sidecar
        assert "email from Bob" in sidecar
        assert "## Reasoning" in sidecar
        assert "primary source" in sidecar

    def test_prints_json_result_line(
        self, tmp_vault: Path, tmp_path: Path, capsys
    ):
        original = write_original(tmp_vault, "note.md", "old\n")
        new = write_new_content(tmp_path, "new\n")

        run_script(tmp_vault, original, new, subject="result-test")

        out = capsys.readouterr().out.strip().splitlines()
        # The last line should be a JSON object with the expected keys
        last = json.loads(out[-1])
        assert "archive_path" in last
        assert "sidecar_path" in last
        assert "slug" in last
        assert last["slug"] == "result-test"
        assert last["archive_path"].endswith("result-test.md")
        assert last["sidecar_path"].endswith("result-test.sidecar.md")

    def test_never_touches_original_file(self, tmp_vault: Path, tmp_path: Path):
        body = "# Original\n\nUnchanged.\n"
        original = write_original(tmp_vault, "note.md", body)
        new = write_new_content(tmp_path, "new\n")

        run_script(tmp_vault, original, new, subject="preserve-test")

        # The whole point: the script does NOT delete or modify the live file.
        assert original.exists()
        assert original.read_text() == body


# ---------------------------------------------------------------------------
# Section anchor mode
# ---------------------------------------------------------------------------


class TestSectionAnchor:
    def test_archives_only_matching_section(
        self, tmp_vault: Path, tmp_path: Path
    ):
        body = textwrap.dedent(
            """\
            # Status

            ## Acme Renewal

            Old date: 2026-06-01

            ## Other Stuff

            Do not archive me.
            """
        )
        original = write_original(tmp_vault, "status.md", body)
        new = write_new_content(tmp_path, "New renewal: 2026-07-15\n")

        run_script(
            tmp_vault,
            original,
            new,
            subject="acme-renewal",
            section_anchor="Acme Renewal",
        )

        archived = (
            tmp_vault
            / "archive"
            / "contradictions"
            / current_month()
            / "acme-renewal.md"
        ).read_text()

        assert "Acme Renewal" in archived
        assert "2026-06-01" in archived
        assert "Other Stuff" not in archived
        assert "Do not archive me." not in archived

    def test_missing_anchor_errors(
        self, tmp_vault: Path, tmp_path: Path, capsys
    ):
        original = write_original(tmp_vault, "note.md", "# Note\n\n## Alpha\n\ntext\n")
        new = write_new_content(tmp_path, "new\n")

        code = run_script(
            tmp_vault,
            original,
            new,
            subject="missing",
            section_anchor="Nonexistent",
        )
        assert code == 1
        err = capsys.readouterr().err
        assert "section anchor not found" in err.lower() or "Nonexistent" in err


# ---------------------------------------------------------------------------
# Slug collision handling
# ---------------------------------------------------------------------------


class TestSlugCollision:
    def test_collision_appends_suffix(self, tmp_vault: Path, tmp_path: Path):
        original = write_original(tmp_vault, "note.md", "old\n")
        new = write_new_content(tmp_path, "new\n")

        # First call
        run_script(tmp_vault, original, new, subject="dup-slug")
        # Second call with the same subject
        run_script(tmp_vault, original, new, subject="dup-slug")
        # Third
        run_script(tmp_vault, original, new, subject="dup-slug")

        archive_dir = tmp_vault / "archive" / "contradictions" / current_month()
        assert (archive_dir / "dup-slug.md").exists()
        assert (archive_dir / "dup-slug-1.md").exists()
        assert (archive_dir / "dup-slug-2.md").exists()
        assert (archive_dir / "dup-slug.sidecar.md").exists()
        assert (archive_dir / "dup-slug-1.sidecar.md").exists()
        assert (archive_dir / "dup-slug-2.sidecar.md").exists()

    def test_collision_on_sidecar_only_still_bumps(
        self, tmp_vault: Path, tmp_path: Path
    ):
        archive_dir = tmp_vault / "archive" / "contradictions" / current_month()
        archive_dir.mkdir(parents=True)
        # Preseed JUST the sidecar so the collision check must look at both names.
        (archive_dir / "ghost.sidecar.md").write_text("pre-existing\n")

        original = write_original(tmp_vault, "note.md", "old\n")
        new = write_new_content(tmp_path, "new\n")

        run_script(tmp_vault, original, new, subject="ghost")

        assert (archive_dir / "ghost.sidecar.md").read_text() == "pre-existing\n"
        assert (archive_dir / "ghost-1.md").exists()
        assert (archive_dir / "ghost-1.sidecar.md").exists()


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_dry_run_no_filesystem_changes(self, tmp_vault: Path, tmp_path: Path):
        original = write_original(tmp_vault, "note.md", "old\n")
        new = write_new_content(tmp_path, "new\n")

        before = {p for p in tmp_vault.rglob("*") if p.is_file()}
        code = run_script(
            tmp_vault, original, new, subject="dry-test", dry_run=True
        )
        after = {p for p in tmp_vault.rglob("*") if p.is_file()}

        assert code == 0
        assert before == after
        assert not (tmp_vault / "archive" / "contradictions").exists()

    def test_dry_run_prints_would_create(
        self, tmp_vault: Path, tmp_path: Path, capsys
    ):
        original = write_original(tmp_vault, "note.md", "old\n")
        new = write_new_content(tmp_path, "new\n")

        run_script(tmp_vault, original, new, subject="dry-print", dry_run=True)
        out = capsys.readouterr().out
        assert "WOULD CREATE" in out
        assert "dry-print.md" in out
        assert "dry-print.sidecar.md" in out


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


class TestErrors:
    def test_missing_original_file(self, tmp_vault: Path, tmp_path: Path, capsys):
        new = write_new_content(tmp_path, "new\n")
        code = run_script(
            tmp_vault,
            tmp_vault / "brain" / "does-not-exist.md",
            new,
            subject="missing-original",
        )
        assert code == 1
        assert "does not exist" in capsys.readouterr().err.lower()

    def test_missing_new_content_file(self, tmp_vault: Path, capsys):
        original = write_original(tmp_vault, "note.md", "old\n")
        code = run_script(
            tmp_vault,
            original,
            Path("/tmp/definitely-not-a-file-xxxxx.md"),
            subject="missing-new",
        )
        assert code == 1
        err = capsys.readouterr().err.lower()
        assert "does not exist" in err

    def test_nonexistent_vault(self, tmp_path: Path, capsys):
        original = tmp_path / "orig.md"
        original.write_text("old\n")
        new = write_new_content(tmp_path, "new\n")

        code = main(
            [
                "/nonexistent/vault/path",
                "--original-file",
                str(original),
                "--new-content-file",
                str(new),
                "--source-description",
                "s",
                "--reasoning",
                "r",
                "--subject",
                "nope",
            ]
        )
        assert code == 1
        assert "vault path" in capsys.readouterr().err.lower()


# ---------------------------------------------------------------------------
# Integration with populated_vault
# ---------------------------------------------------------------------------


class TestPopulatedVault:
    def test_archives_contradiction_in_populated_vault(
        self, populated_vault: Path, tmp_path: Path
    ):
        original = populated_vault / "brain" / "status.md"
        new = write_new_content(
            tmp_path, "Quarterly report was cancelled by [[entities/bob|Bob]].\n"
        )

        code = run_script(
            populated_vault,
            original,
            new,
            subject="quarterly-report-cancelled",
            source="[[brain/session-log#2026-04-10]]",
            reasoning="Bob confirmed cancellation directly",
        )
        assert code == 0

        archive_dir = (
            populated_vault / "archive" / "contradictions" / current_month()
        )
        assert (archive_dir / "quarterly-report-cancelled.md").exists()
        assert (archive_dir / "quarterly-report-cancelled.sidecar.md").exists()
        # Original still untouched
        assert "quarterly report" in original.read_text().lower()
