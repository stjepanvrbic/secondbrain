"""Integration tests for extract_new_turns.py + advance_cursor.py (T12).

Exercises the full round-trip the secondbrain-ingester subagent will perform:
  1. extract envelope from transcript (no cursor yet)
  2. advance cursor to the latest processed message
  3. extract again → should return no new turns
  4. append more transcript lines
  5. extract → only the new lines
  6. simulate a retry (no cursor advance) — re-extract returns same content
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import List

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "secondbrain" / "scripts"
EXTRACT_SCRIPT = SCRIPTS_DIR / "extract_new_turns.py"
ADVANCE_SCRIPT = SCRIPTS_DIR / "advance_cursor.py"


def _run(script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _user_line(uuid: str, content: str) -> str:
    return json.dumps({
        "type": "user",
        "uuid": uuid,
        "message": {"role": "user", "content": content},
        "timestamp": "2026-04-11T10:00:00.000Z",
    })


def _assistant_line(uuid: str, text: str) -> str:
    return json.dumps({
        "type": "assistant",
        "uuid": uuid,
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
        },
        "timestamp": "2026-04-11T10:00:05.000Z",
    })


def _make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    (vault / ".secondbrain" / "cursors").mkdir(parents=True, exist_ok=True)
    return vault


def _write_transcript(path: Path, lines: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def _append_transcript(path: Path, lines: List[str]) -> None:
    existing = path.read_text() if path.exists() else ""
    path.write_text(existing + "\n".join(lines) + "\n")


def _extract(
    vault: Path,
    transcript: Path,
    session_id: str,
    tmp_path: Path,
    cwd: Path | None = None,
) -> dict:
    output = tmp_path / f"env-{session_id}.json"
    if output.exists():
        output.unlink()
    r = _run(
        EXTRACT_SCRIPT,
        "--session", session_id,
        "--transcript", str(transcript),
        "--vault", str(vault),
        "--cwd", str(cwd or tmp_path),
        "--output", str(output),
    )
    assert r.returncode == 0, f"extract failed: {r.stderr}"
    return json.loads(output.read_text())


def _advance(
    cursor_path: Path,
    uuid: str,
    index: int,
    increment: bool = True,
    status: str | None = None,
) -> None:
    args = [
        "--cursor", str(cursor_path),
        "--to-message-uuid", uuid,
        "--to-message-index", str(index),
    ]
    if increment:
        args.append("--increment-ingest-count")
    if status:
        args.extend(["--status", status])
    r = _run(ADVANCE_SCRIPT, *args)
    assert r.returncode == 0, f"advance failed: {r.stderr}"


# ---------------------------------------------------------------------------
# Round-trip: extract → advance → extract returns nothing
# ---------------------------------------------------------------------------

class TestRoundTrip:
    def test_extract_advance_extract_returns_empty(self, tmp_path: Path):
        vault = _make_vault(tmp_path)
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [
            _user_line("u0", "hi"),
            _assistant_line("a0", "hello"),
            _user_line("u1", "follow up"),
            _assistant_line("a1", "response"),
        ])

        # 1. First extract: all 4 messages are new.
        first = _extract(vault, transcript, "sess-rt", tmp_path)
        assert len(first["new_turns"]) == 4

        # 2. Advance cursor to the last processed message.
        last = first["new_turns"][-1]
        cursor = vault / ".secondbrain" / "cursors" / "sess-rt.json"
        _advance(cursor, uuid=last["uuid"], index=last["index"])

        # 3. Extract again: no new turns (cursor is at the end).
        second = _extract(vault, transcript, "sess-rt", tmp_path)
        assert second["new_turns"] == []
        # cursor_state_before now reflects the advanced cursor
        assert second["cursor_state_before"] is not None
        assert second["cursor_state_before"]["last_processed_message_uuid"] == last["uuid"]


# ---------------------------------------------------------------------------
# Incremental extraction as new turns arrive
# ---------------------------------------------------------------------------

class TestIncrementalGrowth:
    def test_new_turns_after_cursor_advance(self, tmp_path: Path):
        vault = _make_vault(tmp_path)
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [
            _user_line("u0", "first"),
            _assistant_line("a0", "first reply"),
        ])

        first = _extract(vault, transcript, "sess-grow", tmp_path)
        assert len(first["new_turns"]) == 2

        last = first["new_turns"][-1]
        cursor = vault / ".secondbrain" / "cursors" / "sess-grow.json"
        _advance(cursor, uuid=last["uuid"], index=last["index"])

        # Append two more turns.
        _append_transcript(transcript, [
            _user_line("u1", "second"),
            _assistant_line("a1", "second reply"),
        ])

        second = _extract(vault, transcript, "sess-grow", tmp_path)
        assert len(second["new_turns"]) == 2
        assert [t["uuid"] for t in second["new_turns"]] == ["u1", "a1"]


# ---------------------------------------------------------------------------
# Retry scenario: ingester fails, cursor doesn't advance
# ---------------------------------------------------------------------------

class TestRetrySafety:
    def test_retry_without_advance_re_extracts_same_content(self, tmp_path: Path):
        vault = _make_vault(tmp_path)
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [
            _user_line("u0", "q0"),
            _assistant_line("a0", "r0"),
        ])
        first = _extract(vault, transcript, "sess-retry", tmp_path)
        # Simulate failure: cursor is NOT advanced.
        # Next Stop-hook run re-extracts — same content comes back.
        second = _extract(vault, transcript, "sess-retry", tmp_path)
        assert len(first["new_turns"]) == len(second["new_turns"])
        assert [t["uuid"] for t in first["new_turns"]] == [t["uuid"] for t in second["new_turns"]]


# ---------------------------------------------------------------------------
# Ingest count accumulates across rounds
# ---------------------------------------------------------------------------

class TestIngestCountAccum:
    def test_increment_after_each_advance(self, tmp_path: Path):
        vault = _make_vault(tmp_path)
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [
            _user_line("u0", "q"),
            _assistant_line("a0", "r"),
        ])
        cursor = vault / ".secondbrain" / "cursors" / "sess-count.json"

        _extract(vault, transcript, "sess-count", tmp_path)
        _advance(cursor, uuid="a0", index=1)
        first = json.loads(cursor.read_text())
        assert first["ingest_count"] == 1

        _append_transcript(transcript, [
            _user_line("u1", "q2"),
            _assistant_line("a1", "r2"),
        ])
        _extract(vault, transcript, "sess-count", tmp_path)
        _advance(cursor, uuid="a1", index=3)
        second = json.loads(cursor.read_text())
        assert second["ingest_count"] == 2


# ---------------------------------------------------------------------------
# Partial-advance recovery: advance only partway through a batch, then re-run
# ---------------------------------------------------------------------------

class TestPartialAdvance:
    def test_partial_advance_leaves_remaining_turns_for_next_run(self, tmp_path: Path):
        vault = _make_vault(tmp_path)
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [
            _user_line("u0", "q0"),
            _assistant_line("a0", "r0"),
            _user_line("u1", "q1"),
            _assistant_line("a1", "r1"),
            _user_line("u2", "q2"),
            _assistant_line("a2", "r2"),
        ])

        first = _extract(vault, transcript, "sess-partial", tmp_path)
        assert len(first["new_turns"]) == 6

        # Only advance halfway through.
        cursor = vault / ".secondbrain" / "cursors" / "sess-partial.json"
        _advance(cursor, uuid="a0", index=1)

        second = _extract(vault, transcript, "sess-partial", tmp_path)
        assert len(second["new_turns"]) == 4
        assert [t["uuid"] for t in second["new_turns"]] == ["u1", "a1", "u2", "a2"]
