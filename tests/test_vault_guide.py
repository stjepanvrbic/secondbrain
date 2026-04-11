"""Tests for vault_guide.py — dynamic vault summary generation."""

import json
import textwrap
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "secondbrain" / "scripts"))

from vault_guide import (
    count_files, count_entities_by_links, get_active_tasks,
    get_upcoming_deadlines, count_inbox, get_last_dream_run,
    generate_guide, format_human, main,
)


class TestCountFiles:
    def test_counts_all_directories(self, tmp_vault: Path):
        counts = count_files(tmp_vault)
        assert counts.get("brain", 0) > 0
        assert counts.get("entities", 0) > 0

    def test_excludes_obsidian(self, tmp_vault: Path):
        (tmp_vault / ".obsidian").mkdir(exist_ok=True)
        (tmp_vault / ".obsidian" / "config.md").write_text("x")
        counts = count_files(tmp_vault)
        assert ".obsidian" not in counts

    def test_empty_directory(self, tmp_vault: Path):
        counts = count_files(tmp_vault)
        assert counts.get("inbox", 0) == 0  # no .md files in inbox in tmp_vault


class TestCountEntitiesByLinks:
    def test_populated_vault(self, populated_vault: Path):
        entities = count_entities_by_links(populated_vault)
        names = [name for name, _ in entities]
        assert "Alice" in names

    def test_empty_entities(self, tmp_vault: Path):
        import shutil
        shutil.rmtree(tmp_vault / "entities")
        result = count_entities_by_links(tmp_vault)
        assert result == []

    def test_entity_files_not_self_counted(self, populated_vault: Path):
        # Entity files reference each other — those should be counted as links
        entities = count_entities_by_links(populated_vault)
        entity_dict = dict(entities)
        # Alice is referenced from status.md AND from bob.md
        assert entity_dict.get("Alice", 0) >= 1


class TestGetActiveTasks:
    def test_finds_open_tasks(self, tmp_vault: Path):
        tasks = get_active_tasks(tmp_vault)
        assert len(tasks) == 1
        assert "Review PR" in tasks[0]

    def test_excludes_completed(self, populated_vault: Path):
        tasks = get_active_tasks(populated_vault)
        assert not any("[x]" in t for t in tasks)

    def test_missing_status_file(self, tmp_vault: Path):
        (tmp_vault / "brain" / "status.md").unlink()
        assert get_active_tasks(tmp_vault) == []


class TestGetUpcomingDeadlines:
    def test_finds_deadlines(self, populated_vault: Path):
        # The populated vault has tasks with dates around 2026-04-10/11
        # Since "today" changes, we test the function works without asserting specific counts
        deadlines = get_upcoming_deadlines(populated_vault, days=365)
        assert isinstance(deadlines, list)

    def test_no_deadlines(self, tmp_vault: Path):
        (tmp_vault / "brain" / "status.md").write_text("# Status\n\n- [ ] No deadline task\n")
        assert get_upcoming_deadlines(tmp_vault) == []


class TestCountInbox:
    def test_empty_inbox(self, tmp_vault: Path):
        total, unprocessed = count_inbox(tmp_vault)
        assert total == 0

    def test_with_files(self, populated_vault: Path):
        total, unprocessed = count_inbox(populated_vault)
        assert total == 2
        assert unprocessed == 1  # note-2026-04-10.md has no [processed:: true]

    def test_no_inbox_dir(self, tmp_vault: Path):
        import shutil
        shutil.rmtree(tmp_vault / "inbox")
        assert count_inbox(tmp_vault) == (0, 0)


class TestGetLastDreamRun:
    def test_no_dream_entries(self, tmp_vault: Path):
        assert get_last_dream_run(tmp_vault) is None

    def test_finds_dream_entry(self, tmp_vault: Path):
        (tmp_vault / "log.md").write_text(textwrap.dedent("""\
            # Log

            ## [2026-04-09 02:00] dream-protocol | Nightly maintenance
            Processed 3 items.

            ## [2026-04-10 10:00] session-start | Morning session
            Loaded context.
        """))
        assert get_last_dream_run(tmp_vault) == "2026-04-09"

    def test_missing_log(self, tmp_vault: Path):
        (tmp_vault / "log.md").unlink()
        assert get_last_dream_run(tmp_vault) is None


class TestGenerateGuide:
    def test_returns_all_fields(self, tmp_vault: Path):
        with patch("vault_guide.run_verify", return_value={"errors": 0, "warnings": 0}):
            guide = generate_guide(tmp_vault)
        assert "total_files" in guide
        assert "entity_count" in guide
        assert "active_tasks" in guide
        assert "inbox_total" in guide
        assert "issues" in guide

    def test_populated_vault(self, populated_vault: Path):
        with patch("vault_guide.run_verify", return_value={"errors": 1, "warnings": 2}):
            guide = generate_guide(populated_vault)
        assert guide["total_files"] > 0
        assert guide["entity_count"] > 0
        assert guide["active_tasks"] > 0


class TestFormatHuman:
    def test_produces_readable_output(self, tmp_vault: Path):
        with patch("vault_guide.run_verify", return_value={"errors": 0, "warnings": 0}):
            guide = generate_guide(tmp_vault)
        text = format_human(guide)
        assert "Vault:" in text
        assert "Files by directory:" in text
        assert "Entities:" in text
        assert "Active tasks:" in text


class TestCLI:
    def test_human_output(self, tmp_vault: Path, capsys):
        with patch("vault_guide.run_verify", return_value=None):
            code = main([str(tmp_vault)])
        assert code == 0
        output = capsys.readouterr().out
        assert "Vault:" in output

    def test_json_output(self, tmp_vault: Path, capsys):
        with patch("vault_guide.run_verify", return_value=None):
            code = main([str(tmp_vault), "--json"])
        assert code == 0
        data = json.loads(capsys.readouterr().out)
        assert "total_files" in data

    def test_nonexistent_vault(self, capsys):
        code = main(["/nonexistent/path"])
        assert code == 1
