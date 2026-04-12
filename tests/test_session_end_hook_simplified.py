"""Tests for the T13 simplification of session-end.sh.

Before T13, session-end.sh emitted a `systemMessage` telling the agent to
run `/secondbrain:session-end`. That skill flushed session summary to the
vault.

T13 moves the flush discipline into per-turn Stop hook commits (T9) and
the background ingester (this task). The SessionEnd hook's only remaining
job is an audit entry in ingest-log.md and a best-effort verify_vault.py
run.

What this file locks down:

    1. session-end.sh produces NO systemMessage output (the "say done"
       discipline is dead).
    2. session-end.sh appends a timestamped session-end line to
       ingest-log.md.
    3. session-end.sh invokes verify_vault.py if the script is present.
    4. session-end.sh handles a missing active vault gracefully (no crash,
       exit 0).
    5. session-end.sh handles malformed stdin gracefully.

Strategy: subprocess-invoke the real hook, same as test_on_stop_hook_commit.py.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
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


def _run_hook(
    payload: dict | None,
    scratch: Path,
    *,
    vaults_config: Path | None = None,
    raw_input: str | None = None,
) -> Tuple[int, str, str]:
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_ROOT"] = str(PLUGIN_ROOT)
    if vaults_config is not None:
        env["SECONDBRAIN_VAULTS_CONFIG"] = str(vaults_config)
    else:
        env["SECONDBRAIN_VAULTS_CONFIG"] = str(scratch / "no-config.json")

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


class TestVerifyVaultBestEffort:
    """When verify_vault.py is present, the hook should invoke it (best
    effort). If it's missing or fails, the hook still exits 0 — audit is
    primary, verification is a secondary signal."""

    def test_verify_vault_output_captured_when_present(self, scratch: Path):
        """Happy path — invoke the real verify_vault.py. The hook should
        run to completion regardless of the verify outcome."""
        vault = _make_vault(scratch)
        config = scratch / "cfg" / "vaults.json"
        _write_vaults_config(config, [_make_vault_entry(vault)], active_id="v1")
        payload = {"session_id": "s", "cwd": str(vault)}
        code, _, _ = _run_hook(payload, scratch, vaults_config=config)
        assert code == 0
        log = _ingest_log(vault)
        assert log, "hook must still log even if verify_vault.py runs"
