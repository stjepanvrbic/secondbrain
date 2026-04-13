"""Tests for the lightweight session-end fallback hook.

Before T13, session-end.sh emitted a `systemMessage` telling the agent to
run `/secondbrain:session-end`. That skill flushed session summary to the
vault.

The steady-state lifecycle path is Stop batching + idle Notification
flushes. SessionEnd is now just a lightweight audit entry plus a final
best-effort ingest flush if there are leftover turns.

What this file locks down:

    1. session-end.sh produces NO systemMessage output (the "say done"
       discipline is dead).
    2. session-end.sh appends a timestamped session-end line to
       ingest-log.md.
    3. session-end.sh does NOT run verify_vault.py inline anymore.
    4. session-end.sh can flush leftover turns via the ingester runner.
    5. session-end.sh handles a missing active vault gracefully (no crash,
       exit 0).
    6. session-end.sh handles malformed stdin gracefully.

Strategy: subprocess-invoke the real hook, same as test_on_stop_hook_commit.py.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Iterator, Tuple

import pytest

HOOK = (
    Path(__file__).resolve().parent.parent
    / "secondbrain"
    / "hooks"
    / "session-end.sh"
)
PLUGIN_ROOT = Path(__file__).resolve().parent.parent / "secondbrain"


@pytest.fixture
def scratch() -> Iterator[Path]:
    raw = tempfile.mkdtemp(prefix="sb_session_end_")
    try:
        yield Path(raw)
    finally:
        shutil.rmtree(raw, ignore_errors=True)


def _write_vaults_config(config_path: Path, vaults: list[dict], active_id: str | None) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "schema_version": 1,
        "vaults": vaults,
        "active_vault_id": active_id,
    }
    config_path.write_text(json.dumps(data, indent=2))


def _make_vault_entry(vault_path: Path, vid: str = "v1") -> dict:
    return {
        "id": vid,
        "path": str(vault_path),
        "name": vault_path.name,
        "role": "personal",
        "added_at": "2026-04-11T12:00:00",
        "with_push": False,
    }


def _make_vault(scratch: Path, name: str = "vault") -> Path:
    vault = scratch / name
    vault.mkdir()
    (vault / "brain").mkdir()
    (vault / "brain" / "status.md").write_text("# Status\n")
    return vault


def _write_transcript(transcript_path: Path, turn_count: int) -> None:
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for i in range(turn_count):
        lines.append(
            json.dumps(
                {
                    "type": "user",
                    "uuid": f"u-{i}",
                    "timestamp": f"2026-04-11T12:{i:02d}:00",
                    "message": {"role": "user", "content": f"user message {i}"},
                }
            )
        )
        lines.append(
            json.dumps(
                {
                    "type": "assistant",
                    "uuid": f"a-{i}",
                    "timestamp": f"2026-04-11T12:{i:02d}:30",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": f"assistant response {i}"}],
                    },
                }
            )
        )
    transcript_path.write_text("\n".join(lines) + "\n")


def _make_stub_claude(bin_dir: Path, log_file: Path) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / "claude"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> "{log_file}"\n'
        "exit 0\n"
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _logged_json_paths(log_file: Path) -> list[Path]:
    if not log_file.exists():
        return []
    out: list[Path] = []
    for token in log_file.read_text().split():
        token = token.rstrip(".'\"")
        if token.startswith("/") and token.endswith(".json"):
            out.append(Path(token))
    return out


def _wait_for_logged_json_paths(log_file: Path, timeout_s: float = 1.0) -> list[Path]:
    deadline = time.time() + timeout_s
    paths = _logged_json_paths(log_file)
    while not paths and time.time() < deadline:
        time.sleep(0.05)
        paths = _logged_json_paths(log_file)
    return paths


def _run_hook(
    payload: dict | None,
    scratch: Path,
    *,
    vaults_config: Path | None = None,
    raw_input: str | None = None,
    path_with_claude: Path | None = None,
    extra_env: dict | None = None,
) -> Tuple[int, str, str]:
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_ROOT"] = str(PLUGIN_ROOT)
    if vaults_config is not None:
        env["SECONDBRAIN_VAULTS_CONFIG"] = str(vaults_config)
    else:
        env["SECONDBRAIN_VAULTS_CONFIG"] = str(scratch / "no-config.json")
    if path_with_claude is not None:
        env["PATH"] = f"{path_with_claude}:/usr/bin:/bin"
    else:
        env["PATH"] = "/usr/bin:/bin"
    if extra_env:
        env.update(extra_env)

    stdin_text = raw_input if raw_input is not None else json.dumps(payload or {})
    result = subprocess.run(
        [str(HOOK)],
        input=stdin_text,
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    return result.returncode, result.stdout, result.stderr


def _ingest_log(vault: Path) -> str:
    p = vault / ".secondbrain" / "ingest-log.md"
    if not p.exists():
        return ""
    return p.read_text()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHookFilePresent:
    def test_hook_exists(self):
        assert HOOK.is_file()

    def test_hook_executable(self):
        assert os.access(HOOK, os.X_OK), (
            f"session-end.sh must be executable; run chmod +x {HOOK}"
        )


class TestNoSystemMessage:
    """The pre-T13 hook emitted a systemMessage with hard-coded text
    telling the agent to run `/secondbrain:session-end`. T13 kills that
    discipline — the hook becomes a silent audit log writer. Asserting
    that stdout is either empty or does not contain a systemMessage
    protects us from someone re-introducing the discipline."""

    def test_stdout_does_not_contain_system_message(self, scratch: Path):
        vault = _make_vault(scratch)
        config = scratch / "cfg" / "vaults.json"
        _write_vaults_config(config, [_make_vault_entry(vault)], active_id="v1")
        payload = {"session_id": "s", "cwd": str(vault)}
        code, out, _ = _run_hook(payload, scratch, vaults_config=config)
        assert code == 0
        assert "systemMessage" not in out, (
            "T13 removed the systemMessage; re-introducing it revives the "
            f"'say done' discipline. Hook stdout: {out!r}"
        )

    def test_stdout_does_not_reference_old_slash_command(self, scratch: Path):
        vault = _make_vault(scratch)
        config = scratch / "cfg" / "vaults.json"
        _write_vaults_config(config, [_make_vault_entry(vault)], active_id="v1")
        payload = {"session_id": "s", "cwd": str(vault)}
        code, out, _ = _run_hook(payload, scratch, vaults_config=config)
        assert code == 0
        assert "secondbrain:session-end" not in out, (
            "T13 retired the /secondbrain:session-end skill; the hook must "
            "not nag about it anymore."
        )


class TestIngestLogAppended:
    """The simplified hook writes a timestamped audit line to
    ingest-log.md whenever it fires against a valid active vault."""

    def test_ingest_log_gets_session_end_entry(self, scratch: Path):
        vault = _make_vault(scratch)
        config = scratch / "cfg" / "vaults.json"
        _write_vaults_config(config, [_make_vault_entry(vault)], active_id="v1")
        payload = {"session_id": "abc-123", "cwd": str(vault)}
        code, _, _ = _run_hook(payload, scratch, vaults_config=config)
        assert code == 0

        log = _ingest_log(vault)
        assert log, "ingest-log.md must exist after session-end fires"
        assert "session-end" in log or "session_end" in log, (
            f"session-end hook must tag its log line as a session-end "
            f"event; got:\n{log}"
        )

    def test_log_includes_session_id(self, scratch: Path):
        vault = _make_vault(scratch)
        config = scratch / "cfg" / "vaults.json"
        _write_vaults_config(config, [_make_vault_entry(vault)], active_id="v1")
        payload = {"session_id": "my-session-xyz-789", "cwd": str(vault)}
        code, _, _ = _run_hook(payload, scratch, vaults_config=config)
        assert code == 0

        log = _ingest_log(vault)
        assert "my-session-xyz-789" in log, (
            "session-end log line must include the session_id for audit"
        )

    def test_ingest_log_creates_missing_directory(self, scratch: Path):
        """If .secondbrain/ doesn't exist yet, the hook must create it."""
        vault = _make_vault(scratch)
        assert not (vault / ".secondbrain").exists()

        config = scratch / "cfg" / "vaults.json"
        _write_vaults_config(config, [_make_vault_entry(vault)], active_id="v1")
        payload = {"session_id": "s", "cwd": str(vault)}
        code, _, _ = _run_hook(payload, scratch, vaults_config=config)
        assert code == 0
        assert (vault / ".secondbrain").exists()
        assert (vault / ".secondbrain" / "ingest-log.md").exists()


class TestNoActiveVaultSilentExit:
    """Pre-init state: vaults.json either doesn't exist or has no active
    vault. The hook must exit 0 silently — no crash, no log writes,
    nothing to audit because there's no vault to audit."""

    def test_missing_config_exits_zero(self, scratch: Path):
        payload = {"session_id": "s", "cwd": str(scratch)}
        # Point at nothing.
        code, out, _ = _run_hook(
            payload,
            scratch,
            vaults_config=scratch / "absent.json",
        )
        assert code == 0
        # No systemMessage, no crash.
        assert "systemMessage" not in out

    def test_empty_vaults_list_exits_zero(self, scratch: Path):
        config = scratch / "cfg" / "vaults.json"
        _write_vaults_config(config, [], active_id=None)
        payload = {"session_id": "s", "cwd": str(scratch)}
        code, _, _ = _run_hook(payload, scratch, vaults_config=config)
        assert code == 0


class TestMalformedStdinGraceful:
    def test_malformed_json_exits_zero(self, scratch: Path):
        code, _, _ = _run_hook(
            None,
            scratch,
            raw_input="this is not {{{ valid json",
        )
        assert code == 0

    def test_empty_stdin_exits_zero(self, scratch: Path):
        code, _, _ = _run_hook(None, scratch, raw_input="")
        assert code == 0


class TestNoInlineVerify:
    """SessionEnd must stay cheap. Inline vault verification was removed."""

    def test_session_end_log_does_not_capture_verify_vault_output(self, scratch: Path):
        vault = _make_vault(scratch)
        config = scratch / "cfg" / "vaults.json"
        _write_vaults_config(config, [_make_vault_entry(vault)], active_id="v1")
        payload = {"session_id": "s", "cwd": str(vault)}
        code, _, _ = _run_hook(payload, scratch, vaults_config=config)
        assert code == 0
        log = _ingest_log(vault)
        assert "verify_vault.py" not in log
        assert '"errors"' not in log
        assert '"warnings"' not in log


class TestFallbackDispatch:
    """If leftover turns still exist at SessionEnd, the hook should flush
    them once via the same ingester path used by Stop/Notification."""

    def test_session_end_dispatches_leftover_turns(self, scratch: Path):
        vault = _make_vault(scratch)
        (vault / ".secondbrain-installed").write_text(json.dumps({"vault_id": "v1"}))
        transcript = scratch / "tx.jsonl"
        _write_transcript(transcript, turn_count=2)

        config = scratch / "cfg" / "vaults.json"
        _write_vaults_config(config, [_make_vault_entry(vault)], active_id="v1")

        bin_dir = scratch / "bin"
        claude_log = scratch / "claude_calls.log"
        _make_stub_claude(bin_dir, claude_log)

        payload = {
            "session_id": "session-end-fallback",
            "transcript_path": str(transcript),
            "cwd": str(vault),
        }
        code, _, _ = _run_hook(
            payload,
            scratch,
            vaults_config=config,
            path_with_claude=bin_dir,
        )
        assert code == 0
        envelope_paths = _wait_for_logged_json_paths(claude_log)
        assert envelope_paths, "session-end fallback should dispatch leftover turns"
        assert envelope_paths[-1].is_file()

    def test_session_end_skips_dispatch_when_nothing_is_pending(self, scratch: Path):
        vault = _make_vault(scratch)
        (vault / ".secondbrain-installed").write_text(json.dumps({"vault_id": "v1"}))
        transcript = scratch / "tx.jsonl"
        transcript.write_text("")

        config = scratch / "cfg" / "vaults.json"
        _write_vaults_config(config, [_make_vault_entry(vault)], active_id="v1")

        bin_dir = scratch / "bin"
        claude_log = scratch / "claude_calls.log"
        _make_stub_claude(bin_dir, claude_log)

        payload = {
            "session_id": "session-end-empty",
            "transcript_path": str(transcript),
            "cwd": str(vault),
        }
        code, _, _ = _run_hook(
            payload,
            scratch,
            vaults_config=config,
            path_with_claude=bin_dir,
        )
        assert code == 0
        assert not claude_log.exists() or claude_log.read_text() == "", (
            "session-end must not dispatch when nothing is pending"
        )
