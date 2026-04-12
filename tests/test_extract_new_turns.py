"""Tests for extract_new_turns.py — T12 transcript extractor.

The script reads a Claude Code transcript JSONL plus a cursor file and writes
an envelope JSON listing only the NEW messages since the cursor. It is
consumed by the secondbrain-ingester subagent (T13) to decide what
conversation content still needs ingesting.

Runs the script as a real subprocess to exercise stdout/stderr/exit-code
contracts end-to-end. Uses synthetic transcripts in tmp_path — never touches
real Claude Code transcripts.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import List

REPO_ROOT = Path(__file__).resolve().parent.parent
EXTRACT_SCRIPT = (
    REPO_ROOT / "secondbrain" / "scripts" / "extract_new_turns.py"
)


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(EXTRACT_SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _make_vault(tmp_path: Path, vault_id: str | None = None) -> Path:
    """Create a minimal vault directory with an optional .secondbrain-installed marker."""
    vault = tmp_path / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    (vault / ".secondbrain").mkdir(parents=True, exist_ok=True)
    (vault / ".secondbrain" / "cursors").mkdir(parents=True, exist_ok=True)
    if vault_id is not None:
        marker = {"vault_id": vault_id, "created": "2026-04-11T00:00:00Z"}
        (vault / ".secondbrain-installed").write_text(json.dumps(marker))
    return vault


def _user_line(uuid: str, content: str) -> str:
    """Build a JSONL line for a user message."""
    return json.dumps({
        "type": "user",
        "uuid": uuid,
        "parentUuid": None,
        "message": {"role": "user", "content": content},
        "timestamp": "2026-04-11T10:00:00.000Z",
        "sessionId": "sess-test",
    })


def _assistant_line(uuid: str, text_blocks: List[str]) -> str:
    """Build a JSONL line for an assistant message with the given text blocks."""
    return json.dumps({
        "type": "assistant",
        "uuid": uuid,
        "parentUuid": None,
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": t} for t in text_blocks],
        },
        "timestamp": "2026-04-11T10:00:05.000Z",
        "sessionId": "sess-test",
    })


def _assistant_line_mixed(uuid: str) -> str:
    """Assistant message with text + tool_use + thinking blocks mixed."""
    return json.dumps({
        "type": "assistant",
        "uuid": uuid,
        "parentUuid": None,
        "message": {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "some private reasoning"},
                {"type": "text", "text": "Let me check the files."},
                {
                    "type": "tool_use",
                    "id": "toolu_abc",
                    "name": "Read",
                    "input": {"file_path": "/tmp/foo.txt"},
                },
                {"type": "text", "text": "Here is the answer."},
            ],
        },
        "timestamp": "2026-04-11T10:00:07.000Z",
        "sessionId": "sess-test",
    })


def _write_transcript(path: Path, lines: List[str]) -> None:
    """Write JSONL lines to a transcript file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def _write_cursor(vault: Path, session_id: str, index: int, uuid: str) -> Path:
    """Write a cursor file at the standard location."""
    cursor_path = vault / ".secondbrain" / "cursors" / f"{session_id}.json"
    cursor_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "session_id": session_id,
        "transcript_path": "",
        "last_processed_message_uuid": uuid,
        "last_processed_message_index": index,
        "last_run_at": "2026-04-11T10:00:00Z",
        "last_run_status": "success",
        "ingest_count": 1,
    }
    cursor_path.write_text(json.dumps(data))
    return cursor_path


# ---------------------------------------------------------------------------
# Script presence
# ---------------------------------------------------------------------------

class TestScriptPresence:
    def test_script_exists(self):
        assert EXTRACT_SCRIPT.is_file()

    def test_help_runs(self):
        r = _run_cli("--help")
        assert r.returncode == 0


# ---------------------------------------------------------------------------
# Missing transcript: no-op
# ---------------------------------------------------------------------------

class TestMissingTranscript:
    def test_missing_transcript_emits_empty_envelope(self, tmp_path: Path):
        vault = _make_vault(tmp_path)
        missing = tmp_path / "no-such-transcript.jsonl"
        output = tmp_path / "envelope.json"
        r = _run_cli(
            "--session", "sess-missing",
            "--transcript", str(missing),
            "--vault", str(vault),
            "--cwd", str(tmp_path),
            "--output", str(output),
        )
        assert r.returncode == 0, r.stderr
        assert output.is_file()
        data = json.loads(output.read_text())
        assert data["new_turns"] == []
        assert data["cursor_state_before"] is None


# ---------------------------------------------------------------------------
# No cursor: include ALL messages
# ---------------------------------------------------------------------------

class TestNoCursor:
    def test_no_cursor_includes_all_messages(self, tmp_path: Path):
        vault = _make_vault(tmp_path)
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [
            _user_line("u1", "first question"),
            _assistant_line("a1", ["first answer"]),
            _user_line("u2", "second question"),
            _assistant_line("a2", ["second answer"]),
        ])
        output = tmp_path / "env.json"
        r = _run_cli(
            "--session", "sess-new",
            "--transcript", str(transcript),
            "--vault", str(vault),
            "--cwd", str(tmp_path),
            "--output", str(output),
        )
        assert r.returncode == 0, r.stderr
        data = json.loads(output.read_text())
        assert len(data["new_turns"]) == 4
        uuids = [t["uuid"] for t in data["new_turns"]]
        assert uuids == ["u1", "a1", "u2", "a2"]
        indices = [t["index"] for t in data["new_turns"]]
        assert indices == [0, 1, 2, 3]
        assert data["cursor_state_before"] is None


# ---------------------------------------------------------------------------
# Cursor at index 2: only newer messages
# ---------------------------------------------------------------------------

class TestCursorAdvances:
    def test_cursor_at_index_two_returns_three_and_later(self, tmp_path: Path):
        vault = _make_vault(tmp_path)
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [
            _user_line("u0", "q0"),
            _assistant_line("a0", ["r0"]),
            _user_line("u1", "q1"),           # index 2
            _assistant_line("a1", ["r1"]),    # index 3
            _user_line("u2", "q2"),           # index 4
        ])
        _write_cursor(vault, "sess-adv", index=2, uuid="u1")

        output = tmp_path / "env.json"
        r = _run_cli(
            "--session", "sess-adv",
            "--transcript", str(transcript),
            "--vault", str(vault),
            "--cwd", str(tmp_path),
            "--output", str(output),
        )
        assert r.returncode == 0, r.stderr
        data = json.loads(output.read_text())
        # Only messages with index > 2 (the cursor's last_processed_message_index)
        # should be in new_turns. Indices 0, 1, 2 are already ingested.
        new_uuids = [t["uuid"] for t in data["new_turns"]]
        assert new_uuids == ["a1", "u2"]
        new_indices = [t["index"] for t in data["new_turns"]]
        assert new_indices == [3, 4]

    def test_cursor_state_before_is_reflected(self, tmp_path: Path):
        vault = _make_vault(tmp_path)
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [
            _user_line("u0", "hi"),
            _assistant_line("a0", ["hello"]),
        ])
        _write_cursor(vault, "sess-cs", index=0, uuid="u0")

        output = tmp_path / "env.json"
        r = _run_cli(
            "--session", "sess-cs",
            "--transcript", str(transcript),
            "--vault", str(vault),
            "--cwd", str(tmp_path),
            "--output", str(output),
        )
        assert r.returncode == 0, r.stderr
        data = json.loads(output.read_text())
        assert data["cursor_state_before"] is not None
        assert data["cursor_state_before"]["last_processed_message_uuid"] == "u0"
        assert data["cursor_state_before"]["last_processed_message_index"] == 0


# ---------------------------------------------------------------------------
# Content extraction: mixed blocks
# ---------------------------------------------------------------------------

class TestContentExtraction:
    def test_assistant_text_blocks_are_concatenated(self, tmp_path: Path):
        vault = _make_vault(tmp_path)
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [
            _assistant_line("a1", ["part one", "part two"]),
        ])
        output = tmp_path / "env.json"
        r = _run_cli(
            "--session", "sess-concat",
            "--transcript", str(transcript),
            "--vault", str(vault),
            "--cwd", str(tmp_path),
            "--output", str(output),
        )
        assert r.returncode == 0, r.stderr
        data = json.loads(output.read_text())
        assert len(data["new_turns"]) == 1
        content = data["new_turns"][0]["content"]
        assert "part one" in content
        assert "part two" in content

    def test_tool_use_blocks_are_dropped(self, tmp_path: Path):
        vault = _make_vault(tmp_path)
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [
            _assistant_line_mixed("a1"),
        ])
        output = tmp_path / "env.json"
        r = _run_cli(
            "--session", "sess-mixed",
            "--transcript", str(transcript),
            "--vault", str(vault),
            "--cwd", str(tmp_path),
            "--output", str(output),
        )
        assert r.returncode == 0, r.stderr
        data = json.loads(output.read_text())
        assert len(data["new_turns"]) == 1
        content = data["new_turns"][0]["content"]
        assert "Let me check the files." in content
        assert "Here is the answer." in content
        # tool_use input should NOT appear
        assert "/tmp/foo.txt" not in content
        assert "toolu_abc" not in content

    def test_thinking_blocks_are_dropped(self, tmp_path: Path):
        vault = _make_vault(tmp_path)
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [
            _assistant_line_mixed("a1"),
        ])
        output = tmp_path / "env.json"
        r = _run_cli(
            "--session", "sess-think",
            "--transcript", str(transcript),
            "--vault", str(vault),
            "--cwd", str(tmp_path),
            "--output", str(output),
        )
        assert r.returncode == 0, r.stderr
        data = json.loads(output.read_text())
        content = data["new_turns"][0]["content"]
        # 'thinking' blocks are not ingested
        assert "some private reasoning" not in content

    def test_user_and_assistant_types_both_included(self, tmp_path: Path):
        vault = _make_vault(tmp_path)
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [
            _user_line("u1", "hello"),
            _assistant_line("a1", ["hi back"]),
        ])
        output = tmp_path / "env.json"
        r = _run_cli(
            "--session", "sess-mix",
            "--transcript", str(transcript),
            "--vault", str(vault),
            "--cwd", str(tmp_path),
            "--output", str(output),
        )
        assert r.returncode == 0, r.stderr
        data = json.loads(output.read_text())
        roles = [t["role"] for t in data["new_turns"]]
        assert "user" in roles
        assert "assistant" in roles

    def test_user_content_as_list_of_blocks(self, tmp_path: Path):
        """User messages can have list-of-blocks content (e.g., tool_result)."""
        vault = _make_vault(tmp_path)
        transcript = tmp_path / "t.jsonl"
        line = json.dumps({
            "type": "user",
            "uuid": "u1",
            "message": {
                "role": "user",
                "content": [
                    {"type": "text", "text": "user text block"},
                ],
            },
            "timestamp": "2026-04-11T10:00:00.000Z",
        })
        _write_transcript(transcript, [line])
        output = tmp_path / "env.json"
        r = _run_cli(
            "--session", "sess-ulist",
            "--transcript", str(transcript),
            "--vault", str(vault),
            "--cwd", str(tmp_path),
            "--output", str(output),
        )
        assert r.returncode == 0, r.stderr
        data = json.loads(output.read_text())
        assert len(data["new_turns"]) == 1
        assert "user text block" in data["new_turns"][0]["content"]


# ---------------------------------------------------------------------------
# Non-turn line types are skipped
# ---------------------------------------------------------------------------

class TestNonTurnLines:
    def test_progress_and_snapshot_lines_are_skipped(self, tmp_path: Path):
        vault = _make_vault(tmp_path)
        transcript = tmp_path / "t.jsonl"
        # Mix in some non-turn types that Claude Code emits
        progress = json.dumps({
            "type": "progress",
            "data": {"type": "hook_progress"},
            "uuid": "p1",
        })
        snapshot = json.dumps({
            "type": "file-history-snapshot",
            "messageId": "snap1",
            "snapshot": {},
        })
        _write_transcript(transcript, [
            progress,
            _user_line("u1", "hello"),
            snapshot,
            _assistant_line("a1", ["hi"]),
        ])
        output = tmp_path / "env.json"
        r = _run_cli(
            "--session", "sess-skip",
            "--transcript", str(transcript),
            "--vault", str(vault),
            "--cwd", str(tmp_path),
            "--output", str(output),
        )
        assert r.returncode == 0, r.stderr
        data = json.loads(output.read_text())
        # Only the two real turns should make it
        assert len(data["new_turns"]) == 2
        uuids = [t["uuid"] for t in data["new_turns"]]
        assert "u1" in uuids
        assert "a1" in uuids
        assert "p1" not in uuids
        assert "snap1" not in uuids


# ---------------------------------------------------------------------------
# Malformed lines / cursor corruption
# ---------------------------------------------------------------------------

class TestResilience:
    def test_malformed_jsonl_line_is_skipped(self, tmp_path: Path):
        vault = _make_vault(tmp_path)
        transcript = tmp_path / "t.jsonl"
        transcript.write_text(
            _user_line("u1", "good") + "\n"
            + "{ this is not json }\n"
            + _assistant_line("a1", ["also good"]) + "\n"
        )
        output = tmp_path / "env.json"
        r = _run_cli(
            "--session", "sess-bad",
            "--transcript", str(transcript),
            "--vault", str(vault),
            "--cwd", str(tmp_path),
            "--output", str(output),
        )
        assert r.returncode == 0, r.stderr
        data = json.loads(output.read_text())
        assert len(data["new_turns"]) == 2
        uuids = [t["uuid"] for t in data["new_turns"]]
        assert "u1" in uuids
        assert "a1" in uuids

    def test_line_without_uuid_gets_synthetic_uuid(self, tmp_path: Path):
        vault = _make_vault(tmp_path)
        transcript = tmp_path / "t.jsonl"
        no_uuid = json.dumps({
            "type": "user",
            "message": {"role": "user", "content": "no uuid here"},
        })
        _write_transcript(transcript, [no_uuid])
        output = tmp_path / "env.json"
        r = _run_cli(
            "--session", "sess-nouuid",
            "--transcript", str(transcript),
            "--vault", str(vault),
            "--cwd", str(tmp_path),
            "--output", str(output),
        )
        assert r.returncode == 0, r.stderr
        data = json.loads(output.read_text())
        assert len(data["new_turns"]) == 1
        uuid_field = data["new_turns"][0]["uuid"]
        assert uuid_field.startswith("synthetic-")

    def test_corrupt_cursor_is_warned_and_all_processed(self, tmp_path: Path):
        vault = _make_vault(tmp_path)
        cursor_path = vault / ".secondbrain" / "cursors" / "sess-corrupt.json"
        cursor_path.parent.mkdir(parents=True, exist_ok=True)
        cursor_path.write_text("{ not valid json")

        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [
            _user_line("u1", "q1"),
            _assistant_line("a1", ["r1"]),
        ])
        output = tmp_path / "env.json"
        r = _run_cli(
            "--session", "sess-corrupt",
            "--transcript", str(transcript),
            "--vault", str(vault),
            "--cwd", str(tmp_path),
            "--output", str(output),
        )
        assert r.returncode == 0
        data = json.loads(output.read_text())
        # Corrupt cursor → treat as no cursor → process all
        assert len(data["new_turns"]) == 2
        # Warning on stderr
        assert "cursor" in r.stderr.lower() or "corrupt" in r.stderr.lower() or "warn" in r.stderr.lower()


# ---------------------------------------------------------------------------
# Vault id / marker
# ---------------------------------------------------------------------------

class TestVaultId:
    def test_vault_id_read_from_marker(self, tmp_path: Path):
        vault = _make_vault(tmp_path, vault_id="test-vault-uuid-1234")
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_user_line("u1", "hi")])
        output = tmp_path / "env.json"
        r = _run_cli(
            "--session", "sess-vid",
            "--transcript", str(transcript),
            "--vault", str(vault),
            "--cwd", str(tmp_path),
            "--output", str(output),
        )
        assert r.returncode == 0, r.stderr
        data = json.loads(output.read_text())
        assert data["vault_id"] == "test-vault-uuid-1234"

    def test_missing_marker_yields_empty_vault_id(self, tmp_path: Path):
        vault = _make_vault(tmp_path, vault_id=None)
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_user_line("u1", "hi")])
        output = tmp_path / "env.json"
        r = _run_cli(
            "--session", "sess-novid",
            "--transcript", str(transcript),
            "--vault", str(vault),
            "--cwd", str(tmp_path),
            "--output", str(output),
        )
        assert r.returncode == 0, r.stderr
        data = json.loads(output.read_text())
        # Missing marker → vault_id is empty string (not a crash)
        assert data["vault_id"] == ""


# ---------------------------------------------------------------------------
# Last-msg-file convenience field
# ---------------------------------------------------------------------------

class TestLastMsgFile:
    def test_last_msg_file_is_read_into_envelope(self, tmp_path: Path):
        vault = _make_vault(tmp_path)
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_user_line("u1", "hi")])
        last_msg = tmp_path / "last.txt"
        last_msg.write_text("This is the latest assistant message.")
        output = tmp_path / "env.json"
        r = _run_cli(
            "--session", "sess-last",
            "--transcript", str(transcript),
            "--vault", str(vault),
            "--cwd", str(tmp_path),
            "--last-msg-file", str(last_msg),
            "--output", str(output),
        )
        assert r.returncode == 0, r.stderr
        data = json.loads(output.read_text())
        assert data["last_assistant_message"] == "This is the latest assistant message."

    def test_last_msg_file_absent_yields_empty_string(self, tmp_path: Path):
        vault = _make_vault(tmp_path)
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_user_line("u1", "hi")])
        output = tmp_path / "env.json"
        r = _run_cli(
            "--session", "sess-no-last",
            "--transcript", str(transcript),
            "--vault", str(vault),
            "--cwd", str(tmp_path),
            "--output", str(output),
        )
        assert r.returncode == 0, r.stderr
        data = json.loads(output.read_text())
        assert data["last_assistant_message"] == ""


# ---------------------------------------------------------------------------
# Output is valid JSON / parent dir is created
# ---------------------------------------------------------------------------

class TestOutputWrite:
    def test_output_is_valid_json_round_trip(self, tmp_path: Path):
        vault = _make_vault(tmp_path)
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [
            _user_line("u1", "with \"quotes\" and \n newlines"),
            _assistant_line("a1", ["curly {brace} test"]),
        ])
        output = tmp_path / "env.json"
        r = _run_cli(
            "--session", "sess-rt",
            "--transcript", str(transcript),
            "--vault", str(vault),
            "--cwd", str(tmp_path),
            "--output", str(output),
        )
        assert r.returncode == 0, r.stderr
        text = output.read_text()
        data = json.loads(text)
        # All required top-level keys present
        assert "session_id" in data
        assert "vault_path" in data
        assert "vault_id" in data
        assert "cwd" in data
        assert "cursor_path" in data
        assert "last_assistant_message" in data
        assert "new_turns" in data
        assert "cursor_state_before" in data

    def test_output_parent_dir_is_created(self, tmp_path: Path):
        vault = _make_vault(tmp_path)
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_user_line("u1", "x")])
        output = tmp_path / "deeply" / "nested" / "envelope.json"
        assert not output.parent.exists()
        r = _run_cli(
            "--session", "sess-mkdir",
            "--transcript", str(transcript),
            "--vault", str(vault),
            "--cwd", str(tmp_path),
            "--output", str(output),
        )
        assert r.returncode == 0, r.stderr
        assert output.is_file()


# ---------------------------------------------------------------------------
# Hard errors
# ---------------------------------------------------------------------------

class TestHardErrors:
    def test_missing_vault_path_is_hard_error(self, tmp_path: Path):
        missing_vault = tmp_path / "no-such-vault"
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_user_line("u1", "hi")])
        output = tmp_path / "env.json"
        r = _run_cli(
            "--session", "sess-novault",
            "--transcript", str(transcript),
            "--vault", str(missing_vault),
            "--cwd", str(tmp_path),
            "--output", str(output),
        )
        assert r.returncode != 0


# ---------------------------------------------------------------------------
# Envelope top-level fields populated correctly
# ---------------------------------------------------------------------------

class TestEnvelopeFields:
    def test_session_id_echoed(self, tmp_path: Path):
        vault = _make_vault(tmp_path)
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_user_line("u1", "q")])
        output = tmp_path / "env.json"
        r = _run_cli(
            "--session", "session-echo",
            "--transcript", str(transcript),
            "--vault", str(vault),
            "--cwd", str(tmp_path),
            "--output", str(output),
        )
        assert r.returncode == 0, r.stderr
        data = json.loads(output.read_text())
        assert data["session_id"] == "session-echo"

    def test_vault_path_echoed(self, tmp_path: Path):
        vault = _make_vault(tmp_path)
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_user_line("u1", "q")])
        output = tmp_path / "env.json"
        r = _run_cli(
            "--session", "sess-vp",
            "--transcript", str(transcript),
            "--vault", str(vault),
            "--cwd", str(tmp_path),
            "--output", str(output),
        )
        assert r.returncode == 0, r.stderr
        data = json.loads(output.read_text())
        assert data["vault_path"] == str(vault)

    def test_cwd_echoed(self, tmp_path: Path):
        vault = _make_vault(tmp_path)
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_user_line("u1", "q")])
        cwd_dir = tmp_path / "work"
        cwd_dir.mkdir()
        output = tmp_path / "env.json"
        r = _run_cli(
            "--session", "sess-cwd",
            "--transcript", str(transcript),
            "--vault", str(vault),
            "--cwd", str(cwd_dir),
            "--output", str(output),
        )
        assert r.returncode == 0, r.stderr
        data = json.loads(output.read_text())
        assert data["cwd"] == str(cwd_dir)

    def test_cursor_path_points_to_canonical_location(self, tmp_path: Path):
        vault = _make_vault(tmp_path)
        transcript = tmp_path / "t.jsonl"
        _write_transcript(transcript, [_user_line("u1", "q")])
        output = tmp_path / "env.json"
        r = _run_cli(
            "--session", "session-cp",
            "--transcript", str(transcript),
            "--vault", str(vault),
            "--cwd", str(tmp_path),
            "--output", str(output),
        )
        assert r.returncode == 0, r.stderr
        data = json.loads(output.read_text())
        expected = str(vault / ".secondbrain" / "cursors" / "session-cp.json")
        assert data["cursor_path"] == expected
