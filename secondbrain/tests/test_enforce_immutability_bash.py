"""Tests for enforce-immutability-bash.sh — blocks Bash writes to inbox/archive."""

import json
import subprocess
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "enforce-immutability-bash.sh"


def run_hook(command: str) -> tuple[int, str, str]:
    """Simulate a PreToolUse Bash hook invocation."""
    payload = json.dumps({
        "tool_name": "Bash",
        "tool_input": {"command": command},
    })
    result = subprocess.run(
        [str(HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.returncode, result.stdout, result.stderr


# ---------------------------------------------------------------------------
# Allowed — reads
# ---------------------------------------------------------------------------

class TestReadsAllowed:
    def test_ls_inbox(self):
        code, _, _ = run_hook("ls ~/cowork/inbox/")
        assert code == 0

    def test_ls_archive(self):
        code, _, _ = run_hook("ls archive/")
        assert code == 0

    def test_cat_inbox_file(self):
        code, _, _ = run_hook("cat inbox/2026-04-10.md")
        assert code == 0

    def test_grep_inbox(self):
        code, _, _ = run_hook("grep -r 'pattern' inbox/")
        assert code == 0

    def test_find_inbox(self):
        code, _, _ = run_hook("find inbox/ -name '*.md'")
        assert code == 0

    def test_head_tail(self):
        code, _, _ = run_hook("head -20 inbox/file.md")
        assert code == 0
        code, _, _ = run_hook("tail -5 archive/log.md")
        assert code == 0

    def test_wc(self):
        code, _, _ = run_hook("wc -l inbox/file.md")
        assert code == 0

    def test_file_stat(self):
        code, _, _ = run_hook("stat inbox/file.md")
        assert code == 0


# ---------------------------------------------------------------------------
# Allowed — commands that don't touch inbox/archive
# ---------------------------------------------------------------------------

class TestUnrelatedAllowed:
    def test_mv_outside(self):
        code, _, _ = run_hook("mv brain/status.md brain/status-old.md")
        assert code == 0

    def test_rm_outside(self):
        code, _, _ = run_hook("rm /tmp/scratch.md")
        assert code == 0

    def test_echo_to_brain(self):
        code, _, _ = run_hook("echo 'content' > brain/status.md")
        assert code == 0

    def test_general_command(self):
        code, _, _ = run_hook("git status")
        assert code == 0

    def test_pytest(self):
        code, _, _ = run_hook("python3 -m pytest tests/")
        assert code == 0

    def test_word_archive_in_content_not_path(self):
        """Strings that contain the word 'archive' but not as a path."""
        code, _, _ = run_hook("echo 'I archived the file' > brain/log.md")
        assert code == 0

    def test_grep_for_inbox_word(self):
        """Searching FOR the word inbox in something else."""
        code, _, _ = run_hook("grep -r 'archive me' brain/")
        assert code == 0


# ---------------------------------------------------------------------------
# Allowed — sanctioned scripts
# ---------------------------------------------------------------------------

class TestSanctionedScriptsAllowed:
    def test_archive_inbox_script(self):
        code, _, _ = run_hook("python3 scripts/archive_inbox.py /Users/me/cowork")
        assert code == 0

    def test_migrate_script(self):
        code, _, _ = run_hook("python3 scripts/migrate_v2_to_v3.py /Users/me/cowork")
        assert code == 0

    def test_archive_inbox_full_path(self):
        code, _, _ = run_hook(
            "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/archive_inbox.py ~/cowork"
        )
        assert code == 0


# ---------------------------------------------------------------------------
# Blocked — mv
# ---------------------------------------------------------------------------

class TestMoveBlocked:
    def test_mv_from_inbox(self):
        code, _, stderr = run_hook("mv inbox/foo.md archive/inbox/2026-04/foo.md")
        assert code == 2
        assert "BLOCKED" in stderr

    def test_mv_to_inbox(self):
        code, _, _ = run_hook("mv brain/foo.md inbox/foo.md")
        assert code == 2

    def test_mv_within_archive(self):
        code, _, _ = run_hook("mv archive/old.md archive/old-renamed.md")
        assert code == 2

    def test_mv_with_vault_prefix(self):
        code, _, _ = run_hook("mv ~/cowork/inbox/foo.md ~/cowork/archive/inbox/foo.md")
        assert code == 2


# ---------------------------------------------------------------------------
# Blocked — rm
# ---------------------------------------------------------------------------

class TestRemoveBlocked:
    def test_rm_inbox_file(self):
        code, _, _ = run_hook("rm inbox/foo.md")
        assert code == 2

    def test_rm_archive_file(self):
        code, _, _ = run_hook("rm archive/commitments-v2.md")
        assert code == 2

    def test_rm_rf_inbox(self):
        code, _, _ = run_hook("rm -rf inbox/")
        assert code == 2

    def test_rm_with_path_prefix(self):
        code, _, _ = run_hook("rm /Users/me/cowork/inbox/note.md")
        assert code == 2


# ---------------------------------------------------------------------------
# Blocked — cp
# ---------------------------------------------------------------------------

class TestCopyBlocked:
    def test_cp_to_inbox(self):
        code, _, _ = run_hook("cp template.md inbox/new.md")
        assert code == 2

    def test_cp_to_archive(self):
        code, _, _ = run_hook("cp file.md archive/file.md")
        assert code == 2


# ---------------------------------------------------------------------------
# Blocked — redirection
# ---------------------------------------------------------------------------

class TestRedirectionBlocked:
    def test_echo_to_inbox(self):
        code, _, _ = run_hook("echo 'content' > inbox/file.md")
        assert code == 2

    def test_append_to_inbox(self):
        code, _, _ = run_hook("echo 'more' >> inbox/log.md")
        assert code == 2

    def test_echo_to_archive(self):
        code, _, _ = run_hook("echo 'x' > archive/note.md")
        assert code == 2

    def test_cat_redirect_to_inbox(self):
        code, _, _ = run_hook("cat foo.md > inbox/copy.md")
        assert code == 2

    def test_heredoc_to_inbox(self):
        code, _, _ = run_hook("cat <<EOF > inbox/file.md\ncontent\nEOF")
        assert code == 2


# ---------------------------------------------------------------------------
# Blocked — sed -i
# ---------------------------------------------------------------------------

class TestSedBlocked:
    def test_sed_i_inbox(self):
        code, _, _ = run_hook("sed -i 's/old/new/' inbox/file.md")
        assert code == 2

    def test_sed_i_archive(self):
        code, _, _ = run_hook("sed -i '' 's/a/b/' archive/file.md")
        assert code == 2


# ---------------------------------------------------------------------------
# Blocked — touch
# ---------------------------------------------------------------------------

class TestTouchBlocked:
    def test_touch_inbox(self):
        code, _, _ = run_hook("touch inbox/new.md")
        assert code == 2

    def test_touch_archive(self):
        code, _, _ = run_hook("touch archive/marker")
        assert code == 2


# ---------------------------------------------------------------------------
# Error messages
# ---------------------------------------------------------------------------

class TestErrorMessages:
    def test_mentions_archive_inbox_script(self):
        _, _, stderr = run_hook("mv inbox/foo.md archive/")
        assert "archive_inbox.py" in stderr

    def test_mentions_migrate_script(self):
        _, _, stderr = run_hook("rm inbox/foo.md")
        assert "migrate_v2_to_v3.py" in stderr

    def test_shows_blocked_command(self):
        _, _, stderr = run_hook("rm inbox/important.md")
        assert "rm inbox/important.md" in stderr


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_command(self):
        code, _, _ = run_hook("")
        assert code == 0

    def test_invalid_json(self):
        result = subprocess.run(
            [str(HOOK)],
            input="not json",
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0

    def test_inbox_in_filename_not_path(self):
        """A file named 'inbox-notes.md' in brain/ should not be blocked."""
        code, _, _ = run_hook("mv brain/old.md brain/inbox-notes.md")
        assert code == 0

    def test_archive_in_filename(self):
        code, _, _ = run_hook("mv brain/foo.md brain/archived-foo.md")
        assert code == 0
