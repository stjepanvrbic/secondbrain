"""Tests for enforce-immutability.sh — PreToolUse hook blocking writes to inbox/archive."""

import json
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "enforce-immutability.sh"


def run_hook(tool_name: str, path: str) -> tuple[int, str, str]:
    """Simulate a PreToolUse hook invocation. Returns (exit_code, stdout, stderr)."""
    payload = json.dumps({
        "tool_name": tool_name,
        "tool_input": {"path": path, "content": "dummy"},
    })
    result = subprocess.run(
        [str(HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.returncode, result.stdout, result.stderr


class TestAllowed:
    """Paths that should be allowed through."""

    def test_brain_status(self):
        code, _, _ = run_hook("mcp__obsidian__vault_patch", "brain/status.md")
        assert code == 0

    def test_entities(self):
        code, _, _ = run_hook("mcp__obsidian__vault_create", "entities/alice.md")
        assert code == 0

    def test_domain_folder(self):
        code, _, _ = run_hook("mcp__obsidian__vault_update", "career/job-transition.md")
        assert code == 0

    def test_root_files(self):
        code, _, _ = run_hook("mcp__obsidian__vault_patch", "_MANIFEST.md")
        assert code == 0

    def test_log_md(self):
        code, _, _ = run_hook("mcp__obsidian__vault_patch", "log.md")
        assert code == 0

    def test_me_profile(self):
        code, _, _ = run_hook("mcp__obsidian__vault_patch", "me/profile.md")
        assert code == 0

    def test_scratch(self):
        code, _, _ = run_hook("mcp__obsidian__vault_create", "scratch/notes.md")
        assert code == 0

    def test_empty_path(self):
        """No path in tool_input — can't check, allow."""
        code, _, _ = run_hook("mcp__obsidian__vault_list", "")
        assert code == 0

    def test_folder_named_inbox_not_at_root(self):
        """inbox as a subdir somewhere else should NOT be blocked."""
        code, _, _ = run_hook("mcp__obsidian__vault_create", "brain/inbox-notes.md")
        assert code == 0

    def test_folder_named_archive_not_at_root(self):
        code, _, _ = run_hook("mcp__obsidian__vault_create", "brain/archived-items.md")
        assert code == 0


class TestBlocksInbox:
    """Writes to inbox/ should be blocked."""

    def test_create_in_inbox(self):
        code, _, stderr = run_hook("mcp__obsidian__vault_create", "inbox/new-note.md")
        assert code == 2
        assert "BLOCKED" in stderr
        assert "inbox" in stderr.lower()

    def test_update_in_inbox(self):
        code, _, stderr = run_hook("mcp__obsidian__vault_update", "inbox/2026-04-10.md")
        assert code == 2
        assert "BLOCKED" in stderr

    def test_patch_in_inbox(self):
        code, _, stderr = run_hook("mcp__obsidian__vault_patch", "inbox/brain-dump.md")
        assert code == 2
        assert "BLOCKED" in stderr

    def test_delete_in_inbox(self):
        code, _, stderr = run_hook("mcp__obsidian__vault_delete", "inbox/old.md")
        assert code == 2

    def test_nested_inbox_path(self):
        code, _, stderr = run_hook("mcp__obsidian__vault_create", "inbox/subdir/file.md")
        assert code == 2
        assert "BLOCKED" in stderr

    def test_inbox_root_itself(self):
        code, _, _ = run_hook("mcp__obsidian__vault_create", "inbox")
        assert code == 2

    def test_leading_slash_inbox(self):
        """/inbox/file should also be blocked (leading slash stripped)."""
        code, _, _ = run_hook("mcp__obsidian__vault_create", "/inbox/file.md")
        assert code == 2


class TestBlocksArchive:
    """Writes to archive/ should be blocked."""

    def test_create_in_archive(self):
        code, _, stderr = run_hook("mcp__obsidian__vault_create", "archive/old.md")
        assert code == 2
        assert "BLOCKED" in stderr
        assert "archive" in stderr.lower()

    def test_update_in_archive(self):
        code, _, _ = run_hook("mcp__obsidian__vault_update", "archive/commitments-v2.md")
        assert code == 2

    def test_nested_archive_inbox(self):
        """archive/inbox/2026-04/file.md should be blocked."""
        code, _, _ = run_hook("mcp__obsidian__vault_create", "archive/inbox/2026-04/processed.md")
        assert code == 2

    def test_delete_from_archive(self):
        code, _, _ = run_hook("mcp__obsidian__vault_delete", "archive/anything.md")
        assert code == 2

    def test_archive_root_itself(self):
        code, _, _ = run_hook("mcp__obsidian__vault_create", "archive")
        assert code == 2


class TestErrorMessages:
    """Error messages should be actionable."""

    def test_inbox_error_mentions_archive_inbox(self):
        _, _, stderr = run_hook("mcp__obsidian__vault_create", "inbox/x.md")
        assert "archive_inbox.py" in stderr

    def test_inbox_error_mentions_ingest(self):
        _, _, stderr = run_hook("mcp__obsidian__vault_create", "inbox/x.md")
        assert "ingest" in stderr.lower()

    def test_archive_error_mentions_scripts(self):
        _, _, stderr = run_hook("mcp__obsidian__vault_create", "archive/x.md")
        assert "archive_inbox.py" in stderr or "migrate" in stderr.lower()


class TestMalformedInput:
    """Hook should fail safely on malformed input."""

    def test_invalid_json_allows(self):
        """Invalid JSON → no path extracted → allow (fail-open for safety)."""
        result = subprocess.run(
            [str(HOOK)],
            input="not json",
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0

    def test_no_tool_input(self):
        result = subprocess.run(
            [str(HOOK)],
            input=json.dumps({"tool_name": "x"}),
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0

    def test_empty_stdin(self):
        result = subprocess.run(
            [str(HOOK)],
            input="",
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
