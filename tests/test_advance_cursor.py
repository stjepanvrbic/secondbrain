"""Tests for advance_cursor.py — T12 cursor-update CLI.

The script atomically updates a secondbrain cursor file that tracks the last
successfully ingested message UUID/index for a Claude Code session. It is
called by the secondbrain-ingester subagent (T13) after a successful ingest
round.

Runs the script as a real subprocess to exercise stdout/stderr/exit-code
contracts end-to-end.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ADVANCE_SCRIPT = (
    REPO_ROOT / "secondbrain" / "scripts" / "advance_cursor.py"
)


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ADVANCE_SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


REQUIRED_FIELDS = {
    "session_id",
    "transcript_path",
    "last_processed_message_uuid",
    "last_processed_message_index",
    "last_run_at",
    "last_run_status",
    "ingest_count",
}


def _write_existing_cursor(path: Path, ingest_count: int = 3) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "session_id": "sess-old",
        "transcript_path": "/tmp/t.jsonl",
        "last_processed_message_uuid": "u-old",
        "last_processed_message_index": 1,
        "last_run_at": "2026-04-10T00:00:00Z",
        "last_run_status": "success",
        "ingest_count": ingest_count,
    }
    path.write_text(json.dumps(data))


# ---------------------------------------------------------------------------
# Script presence
# ---------------------------------------------------------------------------

class TestScriptPresence:
    def test_script_exists(self):
        assert ADVANCE_SCRIPT.is_file()

    def test_help_runs(self):
        r = _run_cli("--help")
        assert r.returncode == 0


# ---------------------------------------------------------------------------
# Fresh cursor creation
# ---------------------------------------------------------------------------

class TestFreshCursor:
    def test_fresh_write_creates_file_with_all_fields(self, tmp_path: Path):
        cursor = tmp_path / "cursors" / "sess-new.json"
        r = _run_cli(
            "--cursor", str(cursor),
            "--to-message-uuid", "u-new",
            "--to-message-index", "5",
        )
        assert r.returncode == 0, r.stderr
        assert cursor.is_file()
        data = json.loads(cursor.read_text())
        for field in REQUIRED_FIELDS:
            assert field in data, f"missing {field}"
        assert data["last_processed_message_uuid"] == "u-new"
        assert data["last_processed_message_index"] == 5
        assert data["last_run_status"] == "success"
        assert data["ingest_count"] == 0  # fresh cursor, not incremented

    def test_fresh_cursor_with_increment_starts_at_one(self, tmp_path: Path):
        cursor = tmp_path / "cursors" / "sess-inc.json"
        r = _run_cli(
            "--cursor", str(cursor),
            "--to-message-uuid", "u1",
            "--to-message-index", "0",
            "--increment-ingest-count",
        )
        assert r.returncode == 0, r.stderr
        data = json.loads(cursor.read_text())
        assert data["ingest_count"] == 1


# ---------------------------------------------------------------------------
# Updating an existing cursor
# ---------------------------------------------------------------------------

class TestUpdateExisting:
    def test_update_preserves_ingest_count_without_increment(self, tmp_path: Path):
        cursor = tmp_path / "cursors" / "sess.json"
        _write_existing_cursor(cursor, ingest_count=3)
        r = _run_cli(
            "--cursor", str(cursor),
            "--to-message-uuid", "u-new",
            "--to-message-index", "10",
        )
        assert r.returncode == 0, r.stderr
        data = json.loads(cursor.read_text())
        assert data["ingest_count"] == 3  # unchanged
        assert data["last_processed_message_uuid"] == "u-new"
        assert data["last_processed_message_index"] == 10

    def test_update_increments_ingest_count(self, tmp_path: Path):
        cursor = tmp_path / "cursors" / "sess.json"
        _write_existing_cursor(cursor, ingest_count=3)
        r = _run_cli(
            "--cursor", str(cursor),
            "--to-message-uuid", "u-new",
            "--to-message-index", "10",
            "--increment-ingest-count",
        )
        assert r.returncode == 0, r.stderr
        data = json.loads(cursor.read_text())
        assert data["ingest_count"] == 4

    def test_update_refreshes_last_run_at(self, tmp_path: Path):
        cursor = tmp_path / "cursors" / "sess.json"
        _write_existing_cursor(cursor)
        before = json.loads(cursor.read_text())["last_run_at"]

        r = _run_cli(
            "--cursor", str(cursor),
            "--to-message-uuid", "u-new",
            "--to-message-index", "10",
        )
        assert r.returncode == 0, r.stderr
        after = json.loads(cursor.read_text())["last_run_at"]
        assert after != before

    def test_update_preserves_session_id_and_transcript_path(self, tmp_path: Path):
        cursor = tmp_path / "cursors" / "sess.json"
        _write_existing_cursor(cursor)
        r = _run_cli(
            "--cursor", str(cursor),
            "--to-message-uuid", "u-new",
            "--to-message-index", "10",
        )
        assert r.returncode == 0, r.stderr
        data = json.loads(cursor.read_text())
        # These fields come from the existing cursor and are preserved
        assert data["session_id"] == "sess-old"
        assert data["transcript_path"] == "/tmp/t.jsonl"

    def test_update_preserves_extra_fields(self, tmp_path: Path):
        """Forward compat: unknown fields in an existing cursor should survive."""
        cursor = tmp_path / "cursors" / "sess.json"
        cursor.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "session_id": "sess",
            "transcript_path": "/tmp/t",
            "last_processed_message_uuid": "u1",
            "last_processed_message_index": 0,
            "last_run_at": "2026-04-10T00:00:00Z",
            "last_run_status": "success",
            "ingest_count": 1,
            "future_field": "please preserve me",
            "nested": {"extra": True},
        }
        cursor.write_text(json.dumps(data))
        r = _run_cli(
            "--cursor", str(cursor),
            "--to-message-uuid", "u-new",
            "--to-message-index", "5",
        )
        assert r.returncode == 0, r.stderr
        final = json.loads(cursor.read_text())
        assert final["future_field"] == "please preserve me"
        assert final["nested"] == {"extra": True}


# ---------------------------------------------------------------------------
# Status override
# ---------------------------------------------------------------------------

class TestStatus:
    def test_status_defaults_to_success(self, tmp_path: Path):
        cursor = tmp_path / "c.json"
        r = _run_cli(
            "--cursor", str(cursor),
            "--to-message-uuid", "u1",
            "--to-message-index", "0",
        )
        assert r.returncode == 0, r.stderr
        data = json.loads(cursor.read_text())
        assert data["last_run_status"] == "success"

    def test_status_failed_is_written(self, tmp_path: Path):
        cursor = tmp_path / "c.json"
        r = _run_cli(
            "--cursor", str(cursor),
            "--to-message-uuid", "u1",
            "--to-message-index", "0",
            "--status", "failed",
        )
        assert r.returncode == 0, r.stderr
        data = json.loads(cursor.read_text())
        assert data["last_run_status"] == "failed"


# ---------------------------------------------------------------------------
# Atomic write + parent directory creation
# ---------------------------------------------------------------------------

class TestAtomicWrite:
    def test_no_tmp_file_left_behind(self, tmp_path: Path):
        cursor = tmp_path / "cursors" / "sess.json"
        r = _run_cli(
            "--cursor", str(cursor),
            "--to-message-uuid", "u1",
            "--to-message-index", "3",
        )
        assert r.returncode == 0, r.stderr
        # No .tmp file next to the final cursor
        stray = [p for p in cursor.parent.iterdir() if p.name.endswith(".tmp")]
        assert stray == []

    def test_missing_parent_dir_is_created(self, tmp_path: Path):
        deep = tmp_path / "a" / "b" / "c" / "sess.json"
        assert not deep.parent.exists()
        r = _run_cli(
            "--cursor", str(deep),
            "--to-message-uuid", "u1",
            "--to-message-index", "0",
        )
        assert r.returncode == 0, r.stderr
        assert deep.is_file()


# ---------------------------------------------------------------------------
# Corrupt existing cursor is overwritten
# ---------------------------------------------------------------------------

class TestCorruptCursor:
    def test_corrupt_existing_is_overwritten(self, tmp_path: Path):
        cursor = tmp_path / "c.json"
        cursor.write_text("{ not valid json")
        r = _run_cli(
            "--cursor", str(cursor),
            "--to-message-uuid", "u-new",
            "--to-message-index", "7",
        )
        assert r.returncode == 0
        data = json.loads(cursor.read_text())
        assert data["last_processed_message_uuid"] == "u-new"
        assert data["last_processed_message_index"] == 7
        # A warning should surface on stderr
        assert "warn" in r.stderr.lower() or "corrupt" in r.stderr.lower() or "invalid" in r.stderr.lower()


# ---------------------------------------------------------------------------
# Concurrent-write safety
# ---------------------------------------------------------------------------

class TestConcurrent:
    def test_two_racing_writes_dont_crash(self, tmp_path: Path):
        cursor = tmp_path / "c.json"
        results: list[subprocess.CompletedProcess[str]] = []

        def runner(idx: int) -> None:
            r = _run_cli(
                "--cursor", str(cursor),
                "--to-message-uuid", f"u-{idx}",
                "--to-message-index", str(idx),
            )
            results.append(r)

        t1 = threading.Thread(target=runner, args=(1,))
        t2 = threading.Thread(target=runner, args=(2,))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        # Both runs completed
        assert len(results) == 2
        # Neither crashed
        for r in results:
            assert r.returncode == 0, r.stderr
        # Final file is still parseable (one write won)
        data = json.loads(cursor.read_text())
        assert data["last_processed_message_uuid"] in ("u-1", "u-2")
        # No .tmp leftovers
        stray = [p for p in cursor.parent.iterdir() if p.name.endswith(".tmp")]
        assert stray == []


# ---------------------------------------------------------------------------
# Session id inference
# ---------------------------------------------------------------------------

class TestSessionIdDefault:
    def test_session_id_defaults_to_cursor_stem_when_fresh(self, tmp_path: Path):
        cursor = tmp_path / "cursors" / "sess-from-name.json"
        r = _run_cli(
            "--cursor", str(cursor),
            "--to-message-uuid", "u1",
            "--to-message-index", "0",
        )
        assert r.returncode == 0, r.stderr
        data = json.loads(cursor.read_text())
        assert data["session_id"] == "sess-from-name"
