"""Tests for the lifecycle batching behavior of on-stop.sh.

T9 established the Stop hook's commit-only behavior
(see test_on_stop_hook_commit.py). The current lifecycle contract layers
batched ingest dispatch on top:

    1. Run extract_new_turns.py to build a UNIQUE envelope per dispatch
    2. Dispatch only once there are 5 completed exchanges since the cursor
    3. Always exit 0 — dispatch failures never wedge the session

Tests here are additive; test_on_stop_hook_commit.py still guards the T9
behavior. This file covers the batching/dispatch surface area:

    - extract_new_turns.py leaves behind a unique envelope the runner can read.
    - The hook skips dispatch (but still commits) when there are fewer than
      5 completed exchanges.
    - The hook skips dispatch (but still commits) when
      SECONDBRAIN_SKIP_INGESTER_DISPATCH=1.
    - The hook skips dispatch (but still commits) when the `claude` CLI
      is not on PATH — we can't depend on the real CLI in tests, so we
      need graceful degradation.
    - When all gates pass, the hook writes a dispatch line to ingest-log.md.

Strategy: same as test_on_stop_hook_commit.py — subprocess-invoke the real
hook script with isolated PATH/env, dropping a stub `claude` binary into
a temp bin dir so we can observe what the hook tried to do without
hitting a real Claude API.
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
    / "on-stop.sh"
)
PLUGIN_ROOT = Path(__file__).resolve().parent.parent / "secondbrain"


# ---------------------------------------------------------------------------
# Fixtures (mirror the test_on_stop_hook_commit.py patterns)
# ---------------------------------------------------------------------------


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.setdefault("GIT_AUTHOR_NAME", "Test User")
    env.setdefault("GIT_AUTHOR_EMAIL", "test@example.invalid")
    env.setdefault("GIT_COMMITTER_NAME", "Test User")
    env.setdefault("GIT_COMMITTER_EMAIL", "test@example.invalid")
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def scratch() -> Iterator[Path]:
    raw = tempfile.mkdtemp(prefix="sb_on_stop_full_")
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


def _make_vault_entry(vault_path: Path, vid: str = "v1", with_push: bool = False) -> dict:
    return {
        "id": vid,
        "path": str(vault_path),
        "name": vault_path.name,
        "role": "personal",
        "added_at": "2026-04-11T12:00:00",
        "with_push": with_push,
    }


def _init_vault_as_git_repo(vault: Path) -> None:
    _git("init", "-q", cwd=vault)
    _git("checkout", "-q", "-b", "main", cwd=vault)
    _git("config", "user.email", "test@example.invalid", cwd=vault)
    _git("config", "user.name", "Test User", cwd=vault)
    (vault / "seed.md").write_text("# seed\n")
    _git("add", "seed.md", cwd=vault)
    _git("commit", "-q", "-m", "seed commit", cwd=vault)


def _make_vault_in_scratch(scratch: Path, name: str = "vault") -> Path:
    vault = scratch / name
    vault.mkdir()
    (vault / "brain").mkdir()
    (vault / "brain" / "status.md").write_text("# Status\n")
    # Marker file so extract_new_turns.py can read vault_id (optional).
    (vault / ".secondbrain-installed").write_text(
        json.dumps({"vault_id": "v1"})
    )
    return vault


def _write_transcript(transcript_path: Path, turn_count: int) -> None:
    """Write a JSONL transcript with N user+assistant turn pairs.

    Uses the real transcript shape extract_new_turns.py expects.
    """
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
                        "content": [
                            {"type": "text", "text": f"assistant response {i}"}
                        ],
                    },
                }
            )
        )
    transcript_path.write_text("\n".join(lines) + "\n")


def _make_stub_claude(bin_dir: Path, log_file: Path) -> None:
    """Drop a fake `claude` script that records its argv to `log_file`.

    The stub exits 0 immediately. The hook uses `nohup ... & disown` so
    we can't observe whether the subprocess finishes — but we CAN observe
    that the hook TRIED to dispatch by looking at the stub's log file,
    which the hook writes to before detaching. In practice the subprocess
    will run the stub, the stub will write the argv, and by the time the
    test checks the log file the stub is long gone.
    """
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


def _wait_for_logged_json_count(log_file: Path, expected: int, timeout_s: float = 1.0) -> list[Path]:
    deadline = time.time() + timeout_s
    paths = _logged_json_paths(log_file)
    while len(paths) < expected and time.time() < deadline:
        time.sleep(0.05)
        paths = _logged_json_paths(log_file)
    return paths


def _run_hook(
    payload: dict,
    scratch: Path,
    *,
    vaults_config: Path | None = None,
    path_with_claude: Path | None = None,
    extra_env: dict | None = None,
) -> Tuple[int, str, str]:
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_ROOT"] = str(PLUGIN_ROOT)
    if vaults_config is not None:
        env["SECONDBRAIN_VAULTS_CONFIG"] = str(vaults_config)
    else:
        env["SECONDBRAIN_VAULTS_CONFIG"] = str(scratch / "no-config.json")

    # Control PATH precisely so the hook either finds our stub `claude`
    # or doesn't, depending on the test.
    if path_with_claude is not None:
        env["PATH"] = f"{path_with_claude}:/usr/bin:/bin"
    else:
        # Strip `claude` from PATH entirely by giving the hook a minimal
        # PATH without our stub.
        env["PATH"] = "/usr/bin:/bin"

    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        [str(HOOK)],
        input=json.dumps(payload),
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


class TestExtractNewTurnsIsCalled:
    """When Stop reaches the batching threshold, it must write a unique
    envelope for the detached ingester runner."""

    def test_envelope_gets_written_for_session(self, scratch: Path):
        vault = _make_vault_in_scratch(scratch)
        _init_vault_as_git_repo(vault)
        (vault / "brain" / "new.md").write_text("# new turn\n")

        transcript = scratch / "tx.jsonl"
        _write_transcript(transcript, turn_count=5)

        config = scratch / "cfg" / "vaults.json"
        _write_vaults_config(
            config, [_make_vault_entry(vault)], active_id="v1"
        )

        # Stub claude so the dispatch path can fire without exploding.
        bin_dir = scratch / "bin"
        claude_log = scratch / "claude_calls.log"
        _make_stub_claude(bin_dir, claude_log)

        session_id = "test-session-abc"
        payload = {
            "session_id": session_id,
            "transcript_path": str(transcript),
            "cwd": str(vault),
            "stop_hook_active": False,
        }
        code, _, _ = _run_hook(
            payload,
            scratch,
            vaults_config=config,
            path_with_claude=bin_dir,
        )
        assert code == 0

        envelope_paths = _wait_for_logged_json_paths(claude_log)
        assert envelope_paths, (
            "batched stop path must hand a JSON envelope path to the ingester; "
            f"stub log was: {claude_log.read_text() if claude_log.exists() else '<absent>'}"
        )
        envelope_path = envelope_paths[-1]
        assert envelope_path.is_file(), (
            f"expected envelope at {envelope_path} after the hook ran; "
            f"nothing was found. ingest-log:\n{_ingest_log(vault)}"
        )
        assert envelope_path != Path(f"/tmp/secondbrain-stop-context-{session_id}.json"), (
            "envelope path must no longer be the old fixed /tmp path; "
            "it must be unique per dispatch to avoid collisions"
        )
        assert "envelopes" in envelope_path.parts, (
            f"expected unique envelope under an envelopes/ runtime dir, got {envelope_path}"
        )
        envelope = json.loads(envelope_path.read_text())
        assert "new_turns" in envelope
        assert envelope.get("session_id") == session_id

    def test_same_session_gets_distinct_envelope_paths_on_separate_dispatches(self, scratch: Path):
        vault = _make_vault_in_scratch(scratch)
        _init_vault_as_git_repo(vault)
        config = scratch / "cfg" / "vaults.json"
        _write_vaults_config(config, [_make_vault_entry(vault)], active_id="v1")

        bin_dir = scratch / "bin"
        claude_log = scratch / "claude_calls.log"
        _make_stub_claude(bin_dir, claude_log)

        transcript = scratch / "tx.jsonl"
        session_id = "same-session"

        for idx, marker in enumerate(("first", "second"), start=1):
            (vault / "brain" / f"{marker}.md").write_text(f"# {marker}\n")
            _write_transcript(transcript, turn_count=5)
            payload = {
                "session_id": session_id,
                "transcript_path": str(transcript),
                "cwd": str(vault),
                "stop_hook_active": False,
            }
            code, _, _ = _run_hook(
                payload,
                scratch,
                vaults_config=config,
                path_with_claude=bin_dir,
            )
            assert code == 0
            _wait_for_logged_json_count(claude_log, idx)

        envelope_paths = _wait_for_logged_json_paths(claude_log)
        assert len(envelope_paths) >= 2, (
            "expected two dispatched envelope paths across two stop runs; "
            f"stub log was: {claude_log.read_text() if claude_log.exists() else '<absent>'}"
        )
        assert envelope_paths[-1] != envelope_paths[-2], (
            "separate dispatches for the same session must not reuse one fixed envelope path"
        )


class TestDispatchGatedByNewTurnCount:
    """Stop batches on completed exchanges. Fewer than 5 assistant turns
    since the cursor must not dispatch yet."""

    def test_four_completed_exchanges_no_dispatch(self, scratch: Path):
        vault = _make_vault_in_scratch(scratch)
        _init_vault_as_git_repo(vault)
        # Dirty change so the commit path fires — we want to prove that
        # T9 behavior still runs even when the batched dispatch gets skipped.
        (vault / "brain" / "new.md").write_text("# dirty\n")

        transcript = scratch / "tx.jsonl"
        _write_transcript(transcript, turn_count=4)

        config = scratch / "cfg" / "vaults.json"
        _write_vaults_config(
            config, [_make_vault_entry(vault)], active_id="v1"
        )

        bin_dir = scratch / "bin"
        claude_log = scratch / "claude_calls.log"
        _make_stub_claude(bin_dir, claude_log)

        session_id = "empty-session"
        payload = {
            "session_id": session_id,
            "transcript_path": str(transcript),
            "cwd": str(vault),
            "stop_hook_active": False,
        }
        code, _, _ = _run_hook(
            payload,
            scratch,
            vaults_config=config,
            path_with_claude=bin_dir,
        )
        assert code == 0

        # Commit still happened (T9 behavior preserved).
        cp = _git("rev-list", "--count", "HEAD", cwd=vault)
        assert int(cp.stdout.strip()) >= 2, (
            "T9 commit must still happen even when the batched dispatch is skipped"
        )

        assert not claude_log.exists() or claude_log.read_text() == "", (
            "sub-threshold stop case must NOT invoke `claude`; "
            f"stub log: {claude_log.read_text() if claude_log.exists() else '<absent>'}"
        )


class TestSkipDispatchEnvVar:
    """SECONDBRAIN_SKIP_INGESTER_DISPATCH=1 is a test-friendly escape hatch
    that short-circuits the dispatch path while leaving the commit path
    and extract_new_turns.py invocation intact. This is the knob the rest
    of the test suite uses to avoid flaking on background subprocess
    timing."""

    def test_skip_env_var_suppresses_dispatch(self, scratch: Path):
        vault = _make_vault_in_scratch(scratch)
        _init_vault_as_git_repo(vault)
        (vault / "brain" / "new.md").write_text("# dirty\n")

        transcript = scratch / "tx.jsonl"
        _write_transcript(transcript, turn_count=5)

        config = scratch / "cfg" / "vaults.json"
        _write_vaults_config(
            config, [_make_vault_entry(vault)], active_id="v1"
        )

        bin_dir = scratch / "bin"
        claude_log = scratch / "claude_calls.log"
        _make_stub_claude(bin_dir, claude_log)

        session_id = "skip-dispatch-session"
        payload = {
            "session_id": session_id,
            "transcript_path": str(transcript),
            "cwd": str(vault),
            "stop_hook_active": False,
        }
        code, _, _ = _run_hook(
            payload,
            scratch,
            vaults_config=config,
            path_with_claude=bin_dir,
            extra_env={"SECONDBRAIN_SKIP_INGESTER_DISPATCH": "1"},
        )
        assert code == 0

        # Even though there were enough completed exchanges AND claude was
        # on PATH, the dispatch was gated off by the env var.
        assert not claude_log.exists() or claude_log.read_text() == "", (
            "SECONDBRAIN_SKIP_INGESTER_DISPATCH=1 must suppress the "
            "ingester dispatch; the stub `claude` was called anyway"
        )


class TestClaudeCliMissingOnPath:
    """If `claude` isn't on PATH (e.g., plugin is installed on a server
    with no CLI), the hook must still exit 0 and commit — dispatch is
    best-effort, not mandatory."""

    def test_missing_claude_cli_does_not_wedge(self, scratch: Path):
        vault = _make_vault_in_scratch(scratch)
        _init_vault_as_git_repo(vault)
        (vault / "brain" / "new.md").write_text("# dirty\n")

        transcript = scratch / "tx.jsonl"
        _write_transcript(transcript, turn_count=5)

        config = scratch / "cfg" / "vaults.json"
        _write_vaults_config(
            config, [_make_vault_entry(vault)], active_id="v1"
        )

        session_id = "no-claude-session"
        payload = {
            "session_id": session_id,
            "transcript_path": str(transcript),
            "cwd": str(vault),
            "stop_hook_active": False,
        }
        # path_with_claude=None → no `claude` binary is visible.
        code, _, _ = _run_hook(
            payload,
            scratch,
            vaults_config=config,
            path_with_claude=None,
        )
        assert code == 0

        # The log should still exist (extract still ran + commit still
        # logged), but no dispatch line should claim we succeeded in
        # firing `claude`.
        log_text = _ingest_log(vault)
        assert log_text, "ingest-log.md should have T9 commit entry"


class TestDispatchLoggedOnSuccess:
    """When all gates pass (new turns, claude on PATH, skip var unset),
    the hook should write a 'dispatched ingester' entry to the log so we
    can audit what fired."""

    def test_dispatch_is_logged_in_ingest_log(self, scratch: Path):
        vault = _make_vault_in_scratch(scratch)
        _init_vault_as_git_repo(vault)
        (vault / "brain" / "new.md").write_text("# dirty\n")

        transcript = scratch / "tx.jsonl"
        _write_transcript(transcript, turn_count=5)

        config = scratch / "cfg" / "vaults.json"
        _write_vaults_config(
            config, [_make_vault_entry(vault)], active_id="v1"
        )

        bin_dir = scratch / "bin"
        claude_log = scratch / "claude_calls.log"
        _make_stub_claude(bin_dir, claude_log)

        session_id = "dispatch-logged-session"
        payload = {
            "session_id": session_id,
            "transcript_path": str(transcript),
            "cwd": str(vault),
            "stop_hook_active": False,
        }
        code, _, _ = _run_hook(
            payload,
            scratch,
            vaults_config=config,
            path_with_claude=bin_dir,
        )
        assert code == 0

        log = _ingest_log(vault)
        # Look for something that says we dispatched — prose can vary.
        assert "dispatch" in log.lower() or "ingester" in log.lower(), (
            f"expected dispatch line in ingest-log; got:\n{log}"
        )
