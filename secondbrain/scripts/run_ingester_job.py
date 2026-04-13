#!/usr/bin/env python3
"""Detached ingester runner with per-session locking."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional


def _now_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _append_log(log_path: Path, message: str) -> None:
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"{_now_utc()} {message}\n")
    except Exception:
        pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read_lock(lock_path: Path) -> Optional[dict]:
    if not lock_path.is_file():
        return None
    try:
        data = json.loads(lock_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _write_lock(lock_path: Path, session_id: str) -> None:
    payload = {
        "pid": os.getpid(),
        "session_id": session_id,
        "created_at": _now_utc(),
    }
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
    except Exception:
        try:
            os.unlink(lock_path)
        except OSError:
            pass
        raise


def _acquire_lock(lock_path: Path, session_id: str, log_path: Path) -> bool:
    for _ in range(2):
        try:
            _write_lock(lock_path, session_id)
            return True
        except FileExistsError:
            data = _read_lock(lock_path)
            pid = int(data.get("pid", -1)) if isinstance(data, dict) else -1
            if _pid_alive(pid):
                _append_log(
                    log_path,
                    f"[runner] session {session_id} already has an active ingester lock; skipping duplicate dispatch",
                )
                return False
            try:
                lock_path.unlink()
            except OSError:
                _append_log(
                    log_path,
                    f"[runner] stale lock cleanup failed for session {session_id}; skipping duplicate dispatch",
                )
                return False
            _append_log(
                log_path,
                f"[runner] recovered stale lock for session {session_id}",
            )
    _append_log(log_path, f"[runner] could not acquire lock for session {session_id}")
    return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_ingester_job.py")
    parser.add_argument("--session", required=True)
    parser.add_argument("--envelope", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--lock", required=True)
    return parser


def _prompt_for_envelope(session_id: str, envelope_path: Path) -> str:
    return f"Process the secondbrain context envelope at {envelope_path}. Session: {session_id}."


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    session_id = args.session
    envelope_path = Path(args.envelope).expanduser()
    log_path = Path(args.log).expanduser()
    lock_path = Path(args.lock).expanduser()

    if not envelope_path.is_file():
        _append_log(log_path, f"[runner] missing envelope for session {session_id}: {envelope_path}")
        return 0

    if not _acquire_lock(lock_path, session_id, log_path):
        return 0

    try:
        claude = shutil.which("claude")
        if not claude:
            _append_log(
                log_path,
                f"[runner] `claude` CLI not on PATH; skipping ingest dispatch for session {session_id}",
            )
            return 0

        _append_log(log_path, f"[runner] starting ingester for session {session_id}")
        with log_path.open("a", encoding="utf-8") as log_fh:
            result = subprocess.run(
                [
                    claude,
                    "--agent",
                    "secondbrain-ingester",
                    "-p",
                    _prompt_for_envelope(session_id, envelope_path),
                ],
                stdin=subprocess.DEVNULL,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                check=False,
            )
        _append_log(
            log_path,
            f"[runner] ingester exited rc={result.returncode} for session {session_id}",
        )
        return 0
    finally:
        try:
            lock_path.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
