#!/usr/bin/env python3
"""
advance_cursor.py — T12 atomic cursor updater for the secondbrain-ingester.

The secondbrain-ingester subagent (T13) calls this script after a successful
ingest round to advance the per-session cursor to the last processed message.
If the ingester fails, the cursor is not advanced and the next Stop-hook run
re-processes the same content (idempotent ingest absorbs the overlap).

Cursor schema (per plan Q32 Alt B):
  {
    "session_id": "...",
    "transcript_path": "...",
    "last_processed_message_uuid": "...",
    "last_processed_message_index": N,
    "last_run_at": "...",         // ISO8601 UTC
    "last_run_status": "success",  // or "failed"
    "ingest_count": N               // total successful ingest rounds
  }

Atomic write: write to `<path>.tmp`, then `os.replace(tmp, final)`. On POSIX
os.replace is atomic — readers see either the old file or the new one, never
a partial write.

Forward compatibility: if the existing cursor has extra fields we don't know
about, we preserve them. Schema evolution without data loss.

Failure modes:
  - Corrupt existing cursor → log warning, overwrite with new state.
  - Missing parent directory → created automatically.
  - Concurrent writes → last-writer-wins (os.replace is atomic, so the file
    is always parseable; which run's update survives is undefined).

Usage:
    python3 advance_cursor.py \
        --cursor <cursor_path> \
        --to-message-uuid <uuid> \
        --to-message-index <int> \
        [--status success|failed] \
        [--increment-ingest-count]

Exit codes:
    0 — cursor written
    non-zero — I/O failure or invalid arguments
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


REQUIRED_FIELDS = (
    "session_id",
    "transcript_path",
    "last_processed_message_uuid",
    "last_processed_message_index",
    "last_run_at",
    "last_run_status",
    "ingest_count",
)


def _log_warn(msg: str) -> None:
    sys.stderr.write("advance_cursor: warning: " + msg + "\n")


def _log_err(msg: str) -> None:
    sys.stderr.write("advance_cursor: error: " + msg + "\n")


# ---------------------------------------------------------------------------
# Arg parsing
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="advance_cursor.py",
        description=(
            "Atomically update a secondbrain per-session cursor file to "
            "mark a new last-processed message."
        ),
    )
    parser.add_argument(
        "--cursor",
        required=True,
        help="Absolute path to the cursor JSON file.",
    )
    parser.add_argument(
        "--to-message-uuid",
        required=True,
        help="UUID of the most recently ingested message.",
    )
    parser.add_argument(
        "--to-message-index",
        required=True,
        type=int,
        help="Integer index of the most recently ingested message.",
    )
    parser.add_argument(
        "--status",
        required=False,
        default="success",
        choices=("success", "failed"),
        help="Run status to record (default: success).",
    )
    parser.add_argument(
        "--increment-ingest-count",
        action="store_true",
        help="Increment the ingest_count field by 1.",
    )
    return parser


# ---------------------------------------------------------------------------
# Cursor I/O
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """UTC timestamp in ISO8601 with trailing 'Z'."""
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_existing(path: Path) -> Optional[Dict[str, Any]]:
    """Return the existing cursor dict, or None on missing/corrupt.

    Corrupt files emit a warning but don't crash — we overwrite them on the
    next write.
    """
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        _log_warn("could not read existing cursor: " + str(exc))
        return None
    if not raw.strip():
        _log_warn("existing cursor is empty: " + str(path))
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        _log_warn("existing cursor is corrupt (invalid JSON): " + str(exc))
        return None
    if not isinstance(data, dict):
        _log_warn("existing cursor is not a JSON object: " + str(path))
        return None
    return data


def _merge_state(
    existing: Optional[Dict[str, Any]],
    *,
    cursor_path: Path,
    to_uuid: str,
    to_index: int,
    status: str,
    increment: bool,
) -> Dict[str, Any]:
    """Build the new cursor dict, preserving forward-compat fields.

    Rules:
      - Start from existing dict if present (to keep any extra fields).
      - Always overwrite: last_processed_message_uuid, last_processed_message_index,
        last_run_at, last_run_status.
      - ingest_count: initialised to 0 for a fresh cursor, bumped by 1 only if
        --increment-ingest-count was passed.
      - session_id / transcript_path: preserved if present; otherwise
        synthesised from the cursor filename (session_id = stem).
    """
    out: Dict[str, Any] = {}
    if existing is not None:
        out.update(existing)

    # Session id: preserve existing, else infer from filename.
    if not isinstance(out.get("session_id"), str) or not out.get("session_id"):
        out["session_id"] = cursor_path.stem

    # transcript_path: preserve existing or default to empty string. The
    # ingester can supply this via a future flag; for now we keep the schema
    # field stable.
    if "transcript_path" not in out or not isinstance(out.get("transcript_path"), str):
        out["transcript_path"] = ""

    out["last_processed_message_uuid"] = to_uuid
    out["last_processed_message_index"] = to_index
    out["last_run_at"] = _now_iso()
    out["last_run_status"] = status

    existing_count = out.get("ingest_count")
    if not isinstance(existing_count, int) or existing_count < 0:
        existing_count = 0
    if increment:
        out["ingest_count"] = existing_count + 1
    else:
        out["ingest_count"] = existing_count

    return out


def _validate(cursor: Dict[str, Any]) -> Optional[str]:
    """Return an error string if cursor is missing a required field, else None."""
    for field in REQUIRED_FIELDS:
        if field not in cursor:
            return "missing required field: " + field
    return None


def _atomic_write(path: Path, cursor: Dict[str, Any]) -> None:
    """Write JSON to `path` atomically via tmp-file + os.replace.

    Parent directory is created if missing. The tmp file is written inside
    the same directory as the final target to ensure `os.replace` crosses
    the same filesystem (atomic rename is only guaranteed within one fs).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp." + str(os.getpid()))
    payload = json.dumps(cursor, indent=2)
    try:
        tmp.write_text(payload, encoding="utf-8")
        os.replace(str(tmp), str(path))
    except OSError:
        # Best-effort cleanup of the tmp file if the replace failed. We do
        # NOT swallow the exception — the caller needs to know the write
        # failed.
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    cursor_path = Path(args.cursor).expanduser()

    existing = _read_existing(cursor_path)
    cursor = _merge_state(
        existing,
        cursor_path=cursor_path,
        to_uuid=args.to_message_uuid,
        to_index=args.to_message_index,
        status=args.status,
        increment=args.increment_ingest_count,
    )

    err = _validate(cursor)
    if err is not None:
        _log_err("internal cursor validation failed: " + err)
        return 2

    try:
        _atomic_write(cursor_path, cursor)
    except OSError as exc:
        _log_err("could not write cursor " + str(cursor_path) + ": " + str(exc))
        return 3

    return 0


if __name__ == "__main__":
    sys.exit(main())
