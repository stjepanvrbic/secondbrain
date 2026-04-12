#!/usr/bin/env python3
"""
extract_new_turns.py — T12 transcript extractor for the secondbrain-ingester.

Reads a Claude Code session transcript (JSONL) plus the session's cursor file
and writes a "context envelope" JSON listing only the NEW messages since the
cursor's last recorded position. The secondbrain-ingester subagent (T13)
consumes this envelope to decide what still needs ingesting on each Stop
hook invocation.

Cursor convention (per plan Q32 Alt B):
  ${VAULT_PATH}/.secondbrain/cursors/<session_id>.json

Envelope shape:
  {
    "session_id": "...",
    "vault_path": "...",
    "vault_id": "...",
    "cwd": "...",
    "cursor_path": "${VAULT_PATH}/.secondbrain/cursors/<session_id>.json",
    "last_assistant_message": "...",
    "new_turns": [
      {"uuid": "...", "index": N, "role": "user|assistant",
       "content": "...", "timestamp": "..."},
      ...
    ],
    "cursor_state_before": {
      "last_processed_message_uuid": "...",
      "last_processed_message_index": N
    } | None
  }

Design principles:
  - Every failure mode that isn't a hard error (missing transcript, corrupt
    cursor, malformed JSONL line) degrades gracefully and still produces a
    parseable envelope. The ingester treats an empty new_turns list as
    "nothing to do".
  - Missing vault path IS a hard error — we refuse to proceed without a
    real place to put the cursor.
  - Stdlib only, Python 3.8+.

Usage:
    python3 extract_new_turns.py \
        --session <session_id> \
        --transcript <path> \
        --vault <vault_path> \
        --cwd <cwd> \
        [--last-msg-file <path>] \
        --output <envelope_json_path>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


CURSOR_DIR_REL = Path(".secondbrain") / "cursors"
MARKER_FILENAME = ".secondbrain-installed"

# Message types we consider "real turns" (everything else, e.g. progress,
# file-history-snapshot, hook_progress, etc., is skipped).
_TURN_TYPES = {"user", "assistant"}


def _log_warn(msg: str) -> None:
    sys.stderr.write("extract_new_turns: warning: " + msg + "\n")


def _log_err(msg: str) -> None:
    sys.stderr.write("extract_new_turns: error: " + msg + "\n")


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="extract_new_turns.py",
        description=(
            "Extract new conversation turns from a Claude Code transcript "
            "(JSONL) using a per-session cursor file. Writes a JSON envelope "
            "the secondbrain-ingester subagent consumes."
        ),
    )
    parser.add_argument("--session", required=True, help="Claude Code session id.")
    parser.add_argument("--transcript", required=True, help="Path to transcript JSONL.")
    parser.add_argument("--vault", required=True, help="Vault root path.")
    parser.add_argument("--cwd", required=True, help="Current working directory for downstream context.")
    parser.add_argument(
        "--last-msg-file",
        required=False,
        default=None,
        help="Optional file containing the last assistant message text.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path to write the envelope JSON.",
    )
    return parser


# ---------------------------------------------------------------------------
# Cursor + marker helpers
# ---------------------------------------------------------------------------

def _read_cursor(cursor_path: Path) -> Optional[Dict[str, Any]]:
    """Return cursor dict or None on missing/corrupt cursor (with warning)."""
    if not cursor_path.is_file():
        return None
    try:
        raw = cursor_path.read_text(encoding="utf-8")
    except OSError as exc:
        _log_warn("could not read cursor " + str(cursor_path) + ": " + str(exc))
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        _log_warn(
            "cursor file is corrupt (invalid JSON) at "
            + str(cursor_path)
            + ": "
            + str(exc)
        )
        return None
    if not isinstance(data, dict):
        _log_warn("cursor at " + str(cursor_path) + " is not a JSON object")
        return None
    return data


def _read_vault_id(vault_path: Path) -> str:
    """Read vault_id from .secondbrain-installed. Returns "" if missing."""
    marker = vault_path / MARKER_FILENAME
    if not marker.is_file():
        return ""
    try:
        raw = marker.read_text(encoding="utf-8")
    except OSError as exc:
        _log_warn("could not read marker " + str(marker) + ": " + str(exc))
        return ""
    if not raw.strip():
        return ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        _log_warn("marker " + str(marker) + " is not valid JSON")
        return ""
    if not isinstance(data, dict):
        return ""
    vid = data.get("vault_id")
    if isinstance(vid, str):
        return vid
    return ""


def _read_last_msg_file(path: Optional[str]) -> str:
    if not path:
        return ""
    p = Path(path)
    if not p.is_file():
        _log_warn("last-msg-file does not exist: " + str(p))
        return ""
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        _log_warn("could not read last-msg-file " + str(p) + ": " + str(exc))
        return ""


# ---------------------------------------------------------------------------
# Transcript parsing
# ---------------------------------------------------------------------------

def _extract_text_from_content(content: Any) -> str:
    """Pull ingestable text out of a message.content field.

    Claude Code transcripts have two content shapes we care about:
      1. A plain string (typical for user messages).
      2. A list of blocks, each with a `type` field. We keep `text` blocks
         and drop `thinking`, `tool_use`, `tool_result`, etc.

    Anything we don't recognise is silently skipped — better to emit an
    empty content string than to crash the ingester over a new block type.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
            # thinking, tool_use, tool_result, etc. are intentionally dropped.
        return "\n".join(parts)
    return ""


def _parse_turn(line_obj: Dict[str, Any], index: int) -> Optional[Dict[str, Any]]:
    """Convert one transcript JSONL line into an envelope turn dict.

    Returns None if the line isn't a conversation turn (e.g. progress event).
    Assigns a synthetic uuid if the line doesn't carry one.
    """
    ltype = line_obj.get("type")
    if ltype not in _TURN_TYPES:
        return None

    message = line_obj.get("message")
    if not isinstance(message, dict):
        return None

    role = message.get("role") or ltype
    if not isinstance(role, str):
        role = str(ltype)

    content = _extract_text_from_content(message.get("content"))

    uuid = line_obj.get("uuid")
    if not isinstance(uuid, str) or not uuid:
        uuid = f"synthetic-{index}"

    timestamp = line_obj.get("timestamp")
    if not isinstance(timestamp, str):
        timestamp = ""

    return {
        "uuid": uuid,
        "index": index,
        "role": role,
        "content": content,
        "timestamp": timestamp,
    }


def _parse_transcript(transcript_path: Path) -> List[Dict[str, Any]]:
    """Stream-parse a JSONL transcript into a list of turn dicts.

    Malformed lines are skipped with a warning; the index counter still
    advances so that surviving lines keep stable ordering. Non-turn line
    types (progress, snapshots, etc.) are skipped without consuming an
    index slot — they're not part of the conversation stream the cursor
    tracks.
    """
    turns: List[Dict[str, Any]] = []
    try:
        with transcript_path.open("r", encoding="utf-8", errors="replace") as fh:
            turn_index = 0
            line_number = 0
            for raw_line in fh:
                line_number += 1
                stripped = raw_line.strip()
                if not stripped:
                    continue
                try:
                    obj = json.loads(stripped)
                except json.JSONDecodeError:
                    _log_warn(
                        "skipping malformed JSONL line "
                        + str(line_number)
                        + " in "
                        + str(transcript_path)
                    )
                    continue
                if not isinstance(obj, dict):
                    continue
                turn = _parse_turn(obj, turn_index)
                if turn is None:
                    continue
                turns.append(turn)
                turn_index += 1
    except OSError as exc:
        _log_warn("could not read transcript " + str(transcript_path) + ": " + str(exc))
    return turns


# ---------------------------------------------------------------------------
# Envelope construction
# ---------------------------------------------------------------------------

def _build_envelope(
    *,
    session_id: str,
    vault_path: Path,
    cwd: str,
    cursor_path: Path,
    last_assistant_message: str,
    new_turns: List[Dict[str, Any]],
    cursor_state_before: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "session_id": session_id,
        "vault_path": str(vault_path),
        "vault_id": _read_vault_id(vault_path),
        "cwd": cwd,
        "cursor_path": str(cursor_path),
        "last_assistant_message": last_assistant_message,
        "new_turns": new_turns,
        "cursor_state_before": cursor_state_before,
    }


def _filter_new_turns(
    all_turns: List[Dict[str, Any]],
    cursor: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Return the suffix of `all_turns` that hasn't been ingested yet.

    The cursor's `last_processed_message_index` is the last index the
    ingester successfully stored. Anything with index > that is new.
    When there's no cursor (or it's corrupt), every turn is new.
    """
    if cursor is None:
        return list(all_turns)
    last_index = cursor.get("last_processed_message_index")
    if not isinstance(last_index, int):
        _log_warn("cursor is missing last_processed_message_index; treating as fresh")
        return list(all_turns)
    return [t for t in all_turns if t["index"] > last_index]


def _cursor_state_before(cursor: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if cursor is None:
        return None
    return {
        "last_processed_message_uuid": cursor.get("last_processed_message_uuid"),
        "last_processed_message_index": cursor.get("last_processed_message_index"),
    }


# ---------------------------------------------------------------------------
# Output I/O
# ---------------------------------------------------------------------------

def _write_output(path: Path, envelope: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(envelope, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    vault_path = Path(args.vault).expanduser()
    if not vault_path.is_dir():
        _log_err("vault path does not exist or is not a directory: " + str(vault_path))
        return 2

    cursor_path = vault_path / CURSOR_DIR_REL / (args.session + ".json")
    transcript_path = Path(args.transcript).expanduser()
    output_path = Path(args.output).expanduser()

    last_assistant_message = _read_last_msg_file(args.last_msg_file)

    # Missing transcript → emit empty envelope, exit 0.
    if not transcript_path.is_file():
        _log_warn("transcript does not exist: " + str(transcript_path))
        envelope = _build_envelope(
            session_id=args.session,
            vault_path=vault_path,
            cwd=args.cwd,
            cursor_path=cursor_path,
            last_assistant_message=last_assistant_message,
            new_turns=[],
            cursor_state_before=None,
        )
        try:
            _write_output(output_path, envelope)
        except OSError as exc:
            _log_err("could not write output " + str(output_path) + ": " + str(exc))
            return 3
        return 0

    cursor = _read_cursor(cursor_path)
    all_turns = _parse_transcript(transcript_path)
    new_turns = _filter_new_turns(all_turns, cursor)

    envelope = _build_envelope(
        session_id=args.session,
        vault_path=vault_path,
        cwd=args.cwd,
        cursor_path=cursor_path,
        last_assistant_message=last_assistant_message,
        new_turns=new_turns,
        cursor_state_before=_cursor_state_before(cursor),
    )

    try:
        _write_output(output_path, envelope)
    except OSError as exc:
        _log_err("could not write output " + str(output_path) + ": " + str(exc))
        return 3

    return 0


if __name__ == "__main__":
    sys.exit(main())
