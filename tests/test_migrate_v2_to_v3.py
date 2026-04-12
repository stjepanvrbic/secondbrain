"""Tests for migrate_v2_to_v3.py — move deprecated files to inbox for re-ingestion."""

from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "secondbrain" / "scripts"))

from migrate_v2_to_v3 import move_to_inbox, migrate, main  # type: ignore[reportMissingImports]


class TestMoveToInbox:
    def test_moves_existing_file(self, tmp_path: Path):
        (tmp_path / "brain").mkdir()
        src = tmp_path / "brain" / "commitments.md"
        src.write_text("# Commitments\n- [ ] Task\n")

        result = move_to_inbox(tmp_path, "brain/commitments.md")
        assert result is True
        assert not src.exists()

        # File should be in inbox with migration prefix
        inbox_files = list((tmp_path / "inbox").iterdir())
        assert len(inbox_files) == 1
        assert inbox_files[0].name.startswith("migration--")
        assert "# Commitments" in inbox_files[0].read_text()

    def test_preserves_content(self, tmp_path: Path):
        (tmp_path / "brain").mkdir()
        content = "# Important stuff\n- [ ] Task A\n- [ ] Task B\n"
        (tmp_path / "brain" / "commitments.md").write_text(content)

        move_to_inbox(tmp_path, "brain/commitments.md")
        inbox_files = list((tmp_path / "inbox").iterdir())
        assert inbox_files[0].read_text() == content

    def test_missing_file_returns_false(self, tmp_path: Path):
        (tmp_path / "brain").mkdir()
        assert move_to_inbox(tmp_path, "brain/commitments.md") is False

    def test_creates_inbox_if_missing(self, tmp_path: Path):
        (tmp_path / "brain").mkdir()
        (tmp_path / "brain" / "commitments.md").write_text("x")
        assert not (tmp_path / "inbox").exists()
        move_to_inbox(tmp_path, "brain/commitments.md")
        assert (tmp_path / "inbox").is_dir()

    def test_collision_gets_timestamp(self, tmp_path: Path):
        (tmp_path / "brain").mkdir()
        (tmp_path / "inbox").mkdir()
        (tmp_path / "brain" / "commitments.md").write_text("new")
        (tmp_path / "inbox" / "migration--brain--commitments.md").write_text("existing")

        move_to_inbox(tmp_path, "brain/commitments.md")

        inbox_files = sorted((tmp_path / "inbox").iterdir())
        assert len(inbox_files) == 2
        # Existing file still there
        assert (tmp_path / "inbox" / "migration--brain--commitments.md").read_text() == "existing"

    def test_dry_run_does_not_move(self, tmp_path: Path):
        (tmp_path / "brain").mkdir()
        (tmp_path / "brain" / "commitments.md").write_text("content")
        move_to_inbox(tmp_path, "brain/commitments.md", dry_run=True)
        assert (tmp_path / "brain" / "commitments.md").exists()
        assert not (tmp_path / "inbox").exists()


class TestMigrate:
    def test_moves_all_deprecated(self, tmp_path: Path):
        (tmp_path / "brain").mkdir()
        (tmp_path / "brain" / "commitments.md").write_text("tasks")

        count = migrate(tmp_path)
        assert count == 1
        assert not (tmp_path / "brain" / "commitments.md").exists()

    def test_no_deprecated_files(self, tmp_path: Path):
        (tmp_path / "brain").mkdir()
        count = migrate(tmp_path)
        assert count == 0

    def test_idempotent(self, tmp_path: Path):
        """After migration, re-running is a no-op."""
        (tmp_path / "brain").mkdir()
        (tmp_path / "brain" / "commitments.md").write_text("tasks")

        migrate(tmp_path)
        count = migrate(tmp_path)
        assert count == 0


class TestMain:
    def test_preserves_all_content(self, tmp_path: Path):
        """Critical: NO data should be lost during migration."""
        (tmp_path / "brain").mkdir()
        important_content = (
            "# Commitments\n\n"
            "## URGENT\n"
            "- [ ] Call amex about AutoPay [due:: 2026-04-10]\n\n"
            "## This Week\n"
            "- [ ] Verify H-1B transfer\n"
        )
        (tmp_path / "brain" / "commitments.md").write_text(important_content)

        code = main([str(tmp_path)])
        assert code == 0

        # Content must exist somewhere in inbox
        inbox_files = list((tmp_path / "inbox").iterdir())
        assert len(inbox_files) == 1
        assert inbox_files[0].read_text() == important_content

    def test_dry_run(self, tmp_path: Path):
        (tmp_path / "brain").mkdir()
        (tmp_path / "brain" / "commitments.md").write_text("tasks")

        code = main([str(tmp_path), "--dry-run"])
        assert code == 0
        assert (tmp_path / "brain" / "commitments.md").exists()

    def test_nonexistent_vault(self, tmp_path: Path):
        code = main([str(tmp_path / "nonexistent")])
        assert code == 1

    def test_clean_vault(self, tmp_path: Path):
        (tmp_path / "brain").mkdir()
        code = main([str(tmp_path)])
        assert code == 0  # nothing to do, success
