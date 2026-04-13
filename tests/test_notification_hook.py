"""Tests for the idle Notification hook.

Notification idle_prompt is the real "session went idle" signal. It must
flush leftover turns that never hit the 5-exchange Stop threshold.
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
    / "on-notification.sh"
)
PLUGIN_ROOT = Path(__file__).resolve().parent.parent / "secondbrain"


@pytest.fixture
def scratch() -> Iterator[Path]:
    raw = tempfile.mkdtemp(prefix="sb_notification_")
    try:
        yield Path(raw)
    finally:
        shutil.rmtree(raw, ignore_errors=True)


def _write_vaults_config(config_path: Path, vaults: list[dict], active_id: str | None) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "vaults": vaults,
                "active_vault_id": active_id,
            },
            indent=2,
        )
    )


def _make_vault_entry(vault_path: Path, vid: str = "v1") -> dict:
    return {
        "id": vid,
        "path": str(vault_path),
        "name": vault_path.name,
        "role": "personal",
        "added_at": "2026-04-11T12:00:00",
        "with_push": False,
    }


def _make_vault(scratch: Path) -> Path:
    vault = scratch / "vault"
    vault.mkdir()
    (vault / "brain").mkdir()
    (vault / "brain" / "status.md").write_text("# Status\n")
    (vault / ".secondbrain-installed").write_text(json.dumps({"vault_id": "v1"}))
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
    if path_with_claude is not None:
        env["PATH"] = f"{path_with_claude}:/usr/bin:/bin"
    else:
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


class TestHookFilePresent:
    def test_hook_exists(self):
        assert HOOK.is_file()

    def test_hook_executable(self):
        assert os.access(HOOK, os.X_OK), (
            f"on-notification.sh must be executable; run chmod +x {HOOK}"
        )


class TestIdlePromptFlush:
    def test_idle_prompt_dispatches_below_stop_threshold(self, scratch: Path):
        vault = _make_vault(scratch)
        transcript = scratch / "tx.jsonl"
        _write_transcript(transcript, turn_count=2)

        config = scratch / "cfg" / "vaults.json"
        _write_vaults_config(config, [_make_vault_entry(vault)], active_id="v1")

        bin_dir = scratch / "bin"
        claude_log = scratch / "claude_calls.log"
        _make_stub_claude(bin_dir, claude_log)

        payload = {
            "notification_type": "idle_prompt",
            "session_id": "idle-session",
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
        assert envelope_paths, "idle_prompt must flush leftover turns"
        assert envelope_paths[-1].is_file()

    def test_non_idle_notification_is_ignored(self, scratch: Path):
        vault = _make_vault(scratch)
        transcript = scratch / "tx.jsonl"
        _write_transcript(transcript, turn_count=2)

        config = scratch / "cfg" / "vaults.json"
        _write_vaults_config(config, [_make_vault_entry(vault)], active_id="v1")

        bin_dir = scratch / "bin"
        claude_log = scratch / "claude_calls.log"
        _make_stub_claude(bin_dir, claude_log)

        payload = {
            "notification_type": "message",
            "session_id": "non-idle-session",
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
            "non-idle notifications must not dispatch the ingester"
        )

    def test_skip_env_var_suppresses_idle_dispatch(self, scratch: Path):
        vault = _make_vault(scratch)
        transcript = scratch / "tx.jsonl"
        _write_transcript(transcript, turn_count=2)

        config = scratch / "cfg" / "vaults.json"
        _write_vaults_config(config, [_make_vault_entry(vault)], active_id="v1")

        bin_dir = scratch / "bin"
        claude_log = scratch / "claude_calls.log"
        _make_stub_claude(bin_dir, claude_log)

        payload = {
            "notification_type": "idle_prompt",
            "session_id": "idle-skip-session",
            "transcript_path": str(transcript),
            "cwd": str(vault),
        }
        code, _, _ = _run_hook(
            payload,
            scratch,
            vaults_config=config,
            path_with_claude=bin_dir,
            extra_env={"SECONDBRAIN_SKIP_INGESTER_DISPATCH": "1"},
        )
        assert code == 0
        assert not claude_log.exists() or claude_log.read_text() == "", (
            "SECONDBRAIN_SKIP_INGESTER_DISPATCH=1 must suppress idle dispatch"
        )

    def test_missing_claude_cli_does_not_wedge_idle_hook(self, scratch: Path):
        vault = _make_vault(scratch)
        transcript = scratch / "tx.jsonl"
        _write_transcript(transcript, turn_count=2)

        config = scratch / "cfg" / "vaults.json"
        _write_vaults_config(config, [_make_vault_entry(vault)], active_id="v1")

        payload = {
            "notification_type": "idle_prompt",
            "session_id": "idle-no-claude",
            "transcript_path": str(transcript),
            "cwd": str(vault),
        }
        code, _, _ = _run_hook(
            payload,
            scratch,
            vaults_config=config,
            path_with_claude=None,
        )
        assert code == 0
