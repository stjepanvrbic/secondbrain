"""Tests for the detached ingester runner.

The runner owns per-session mutual exclusion. Hook wrappers may fire
concurrently, but only one real ingester process may run for a session.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Iterator, Tuple

import pytest

RUNNER = (
    Path(__file__).resolve().parent.parent
    / "secondbrain"
    / "scripts"
    / "run_ingester_job.py"
)


@pytest.fixture
def scratch() -> Iterator[Path]:
    raw = tempfile.mkdtemp(prefix="sb_run_ingester_")
    try:
        yield Path(raw)
    finally:
        shutil.rmtree(raw, ignore_errors=True)


def _write_envelope(path: Path, session_id: str = "s1") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "session_id": session_id,
                "vault_path": "/tmp/vault",
                "vault_id": "v1",
                "cwd": "/tmp/vault",
                "cursor_path": "/tmp/vault/.secondbrain/cursors/s1.json",
                "last_assistant_message": "",
                "new_turns": [{"uuid": "u1", "index": 0, "role": "user", "content": "x"}],
                "cursor_state_before": None,
            }
        )
    )


def _make_stub_claude(bin_dir: Path, log_file: Path) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / "claude"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'echo \"$@\" >> \"{log_file}\"\n'
        "exit 0\n"
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _write_lock(path: Path, *, pid: int, session_id: str = "s1") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "pid": pid,
                "session_id": session_id,
            }
        )
    )


def _run_runner(
    envelope: Path,
    log_path: Path,
    lock_path: Path,
    *,
    path_with_claude: Path | None,
) -> Tuple[int, str, str]:
    env = os.environ.copy()
    if path_with_claude is not None:
        env["PATH"] = f"{path_with_claude}:/usr/bin:/bin"
    else:
        env["PATH"] = "/usr/bin:/bin"
    result = subprocess.run(
        [
            "python3",
            str(RUNNER),
            "--session",
            "s1",
            "--envelope",
            str(envelope),
            "--log",
            str(log_path),
            "--lock",
            str(lock_path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    return result.returncode, result.stdout, result.stderr


class TestRunnerFilePresent:
    def test_runner_exists(self):
        assert RUNNER.is_file()


class TestLocking:
    def test_runner_invokes_claude_when_lock_is_free(self, scratch: Path):
        envelope = scratch / "env.json"
        log_path = scratch / "runner.log"
        lock_path = scratch / "locks" / "s1.lock.json"
        claude_log = scratch / "claude.log"
        bin_dir = scratch / "bin"

        _write_envelope(envelope)
        _make_stub_claude(bin_dir, claude_log)

        code, _, _ = _run_runner(
            envelope,
            log_path,
            lock_path,
            path_with_claude=bin_dir,
        )
        assert code == 0
        assert claude_log.exists() and claude_log.read_text().strip(), (
            "runner must invoke claude when no lock is held"
        )
        assert not lock_path.exists(), "runner must remove the lock on exit"

    def test_live_lock_suppresses_duplicate_dispatch(self, scratch: Path):
        envelope = scratch / "env.json"
        log_path = scratch / "runner.log"
        lock_path = scratch / "locks" / "s1.lock.json"
        claude_log = scratch / "claude.log"
        bin_dir = scratch / "bin"

        _write_envelope(envelope)
        _make_stub_claude(bin_dir, claude_log)
        _write_lock(lock_path, pid=os.getpid())

        code, _, _ = _run_runner(
            envelope,
            log_path,
            lock_path,
            path_with_claude=bin_dir,
        )
        assert code == 0
        assert not claude_log.exists() or claude_log.read_text() == "", (
            "runner must not invoke claude when a live session lock already exists"
        )

    def test_stale_lock_is_recovered_and_dispatch_proceeds(self, scratch: Path):
        envelope = scratch / "env.json"
        log_path = scratch / "runner.log"
        lock_path = scratch / "locks" / "s1.lock.json"
        claude_log = scratch / "claude.log"
        bin_dir = scratch / "bin"

        _write_envelope(envelope)
        _make_stub_claude(bin_dir, claude_log)
        _write_lock(lock_path, pid=999999)

        code, _, _ = _run_runner(
            envelope,
            log_path,
            lock_path,
            path_with_claude=bin_dir,
        )
        assert code == 0
        assert claude_log.exists() and claude_log.read_text().strip(), (
            "runner must recover stale locks and dispatch"
        )
        assert not lock_path.exists(), "stale lock should be replaced and cleaned up"
