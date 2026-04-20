#!/usr/bin/env python3
"""Lifecycle hook orchestration for secondbrain.

Owns the shared logic for:
  - Stop hook batching
  - idle Notification flushes
  - SessionEnd fallback flushes

Shell hooks stay as thin wrappers that exec this script. The point is to keep
decision logic in one place where tests can lock behavior down.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional


BATCH_EXCHANGE_THRESHOLD = 5
ENVELOPE_DIR_REL = Path(".secondbrain") / "envelopes"
LOCK_DIR_REL = Path(".secondbrain") / "locks"


def _now_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _plugin_root() -> Path:
    raw = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parent.parent


def _script_path(name: str) -> Path:
    return _plugin_root() / "scripts" / name


def _parse_stdin_payload() -> dict[str, Any]:
    raw = os.environ.get("SECONDBRAIN_HOOK_INPUT")
    if raw is None:
        raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw else {}
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _config_path() -> Path:
    override = os.environ.get("SECONDBRAIN_VAULTS_CONFIG")
    if override:
        return Path(override)
    return Path.home() / ".config" / "secondbrain" / "vaults.json"


def _load_active_vault() -> Optional[dict[str, Any]]:
    cfg = _config_path()
    if not cfg.is_file():
        return None
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    active_id = data.get("active_vault_id")
    if not active_id:
        return None
    vaults = data.get("vaults", [])
    if not isinstance(vaults, list):
        return None
    for entry in vaults:
        if isinstance(entry, dict) and entry.get("id") == active_id:
            path = entry.get("path")
            if isinstance(path, str) and path:
                vault_path = Path(path).expanduser()
                if vault_path.is_dir():
                    return entry
    return None


def _log_path(vault_path: Path) -> Path:
    return vault_path / ".secondbrain" / "ingest-log.md"


def _append_log(log_path: Path, message: str) -> None:
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"{_now_utc()} {message}\n")
    except Exception:
        pass


def _append_multiline_log(log_path: Path, header: str, body: str) -> None:
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"## [{_now_utc()}] {header}\n")
            lines = body.splitlines() or ["(no output)"]
            for line in lines:
                fh.write(f"    {line}\n")
            fh.write("\n")
    except Exception:
        pass


def _safe_session_id(session_id: str) -> str:
    keep = []
    for ch in session_id:
        if ch.isalnum() or ch in ("-", "_"):
            keep.append(ch)
        else:
            keep.append("_")
    return "".join(keep) or "unknown-session"


def _build_envelope_path(vault_path: Path, session_id: str, event: str) -> Path:
    safe_session = _safe_session_id(session_id)
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    unique = f"{time.time_ns()}-{os.getpid()}"
    return (
        vault_path
        / ENVELOPE_DIR_REL
        / f"{safe_session}-{event}-{stamp}-{unique}.json"
    )


def _lock_path(vault_path: Path, session_id: str) -> Path:
    return vault_path / LOCK_DIR_REL / f"{_safe_session_id(session_id)}.json"


def _extract_envelope(
    *,
    payload: dict[str, Any],
    vault_path: Path,
    log_path: Path,
    event: str,
) -> Optional[Path]:
    session_id = str(payload.get("session_id") or "")
    if not session_id:
        return None
    transcript_path = str(payload.get("transcript_path") or "")
    cwd_value = str(payload.get("cwd") or "") or str(vault_path)
    envelope_path = _build_envelope_path(vault_path, session_id, event)
    cmd = [
        sys.executable,
        str(_script_path("extract_new_turns.py")),
        "--session",
        session_id,
        "--transcript",
        transcript_path,
        "--vault",
        str(vault_path),
        "--cwd",
        cwd_value,
        "--output",
        str(envelope_path),
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    combined = "".join(part for part in (result.stdout, result.stderr) if part)
    if combined.strip():
        _append_multiline_log(
            log_path,
            f"{event} | extract_new_turns.py (rc={result.returncode})",
            combined,
        )
    if result.returncode != 0 or not envelope_path.is_file():
        _append_log(
            log_path,
            f"[{event}] extract_new_turns failed (rc={result.returncode}) for session {session_id}",
        )
        return None
    return envelope_path


def _load_envelope(path: Path) -> Optional[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _completed_exchange_count(new_turns: Any) -> int:
    if not isinstance(new_turns, list):
        return 0
    return sum(
        1
        for turn in new_turns
        if isinstance(turn, dict) and turn.get("role") == "assistant"
    )


def _should_dispatch(event: str, envelope: dict[str, Any]) -> tuple[bool, str]:
    new_turns = envelope.get("new_turns", [])
    total_turns = len(new_turns) if isinstance(new_turns, list) else 0
    completed_exchanges = _completed_exchange_count(new_turns)

    if event == "stop":
        if completed_exchanges >= BATCH_EXCHANGE_THRESHOLD:
            return True, f"{completed_exchanges} completed exchanges reached threshold"
        return False, (
            f"{completed_exchanges} completed exchanges below threshold "
            f"{BATCH_EXCHANGE_THRESHOLD}"
        )

    if total_turns > 0:
        return True, f"{total_turns} pending turns"
    return False, "no pending turns"


def _submit_runner(
    *,
    session_id: str,
    envelope_path: Path,
    log_path: Path,
    lock_path: Path,
) -> bool:
    runner = _script_path("run_ingester_job.py")
    if not runner.is_file():
        _append_log(log_path, f"[runner] missing run_ingester_job.py for session {session_id}")
        return False
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log_fh:
        subprocess.Popen(
            [
                sys.executable,
                str(runner),
                "--session",
                session_id,
                "--envelope",
                str(envelope_path),
                "--log",
                str(log_path),
                "--lock",
                str(lock_path),
            ],
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    return True


def _maybe_dispatch(event: str, payload: dict[str, Any], vault_path: Path) -> None:
    log_path = _log_path(vault_path)
    session_id = str(payload.get("session_id") or "")
    if not session_id:
        return

    envelope_path = _extract_envelope(
        payload=payload,
        vault_path=vault_path,
        log_path=log_path,
        event=event,
    )
    if envelope_path is None:
        return

    envelope = _load_envelope(envelope_path)
    if envelope is None:
        _append_log(
            log_path,
            f"[{event}] envelope unreadable; skipping dispatch for session {session_id}",
        )
        return

    if os.environ.get("SECONDBRAIN_SKIP_INGESTER_DISPATCH") == "1":
        _append_log(
            log_path,
            f"[{event}] SECONDBRAIN_SKIP_INGESTER_DISPATCH=1; skipping dispatch for session {session_id}",
        )
        return

    should_dispatch, reason = _should_dispatch(event, envelope)
    if not should_dispatch:
        _append_log(log_path, f"[{event}] {reason}; skipping dispatch for session {session_id}")
        return

    if _submit_runner(
        session_id=session_id,
        envelope_path=envelope_path,
        log_path=log_path,
        lock_path=_lock_path(vault_path, session_id),
    ):
        _append_log(
            log_path,
            f"[{event}] dispatched ingester for session {session_id} using {envelope_path} ({reason})",
        )


def _handle_stop_hook() -> int:
    payload = _parse_stdin_payload()
    if bool(payload.get("stop_hook_active", False)):
        return 0
    entry = _load_active_vault()
    if entry is None:
        return 0
    vault_path = Path(str(entry["path"])).expanduser()
    _maybe_dispatch("stop", payload, vault_path)
    return 0


def _handle_notification_hook() -> int:
    payload = _parse_stdin_payload()
    if str(payload.get("notification_type") or "") != "idle_prompt":
        return 0
    entry = _load_active_vault()
    if entry is None:
        return 0
    vault_path = Path(str(entry["path"])).expanduser()
    _maybe_dispatch("notification", payload, vault_path)
    return 0


def _handle_session_end_hook() -> int:
    payload = _parse_stdin_payload()
    entry = _load_active_vault()
    if entry is None:
        return 0
    vault_path = Path(str(entry["path"])).expanduser()
    log_path = _log_path(vault_path)
    session_id = str(payload.get("session_id") or "")
    _append_log(log_path, f"[session-end] session {session_id} ended")
    _maybe_dispatch("session-end", payload, vault_path)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lifecycle_ingest.py")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("stop-hook")
    subparsers.add_parser("notification-hook")
    subparsers.add_parser("session-end-hook")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "stop-hook":
        return _handle_stop_hook()
    if args.command == "notification-hook":
        return _handle_notification_hook()
    if args.command == "session-end-hook":
        return _handle_session_end_hook()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
