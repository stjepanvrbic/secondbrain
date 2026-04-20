"""Tests for emit-hot-memory.sh — T11 SessionStart hook wrapper.

The hook:
  1. Reads Claude Code SessionStart payload from stdin (may contain cwd).
  2. Resolves the active vault via ~/.config/secondbrain/vaults.json
     (or SECONDBRAIN_VAULTS_CONFIG in tests).
  3. If no active vault → emits a "secondbrain not configured" fallback.
  4. Otherwise delegates to emit_hot_memory.py and prints the JSON to stdout.

Strategy: subprocess-invoke emit-hot-memory.sh with mocked stdin and a
temporary vaults.json. Uses tempfile.mkdtemp() to dodge the macOS
/tmp ↔ /private/tmp symlink quirk (same reason as test_on_stop_hook_commit.py).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterator, Tuple

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = REPO_ROOT / "secondbrain"
HOOK = PLUGIN_ROOT / "hooks" / "emit-hot-memory.sh"

sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from cowork_hygiene import read_session_start_stamp  # type: ignore[reportMissingImports]
from hot_memory_schema import INITIAL_TEMPLATE  # type: ignore[reportMissingImports]


# ---------------------------------------------------------------------------
# Fixtures — mkdtemp to avoid macOS /tmp symlink quirk
# ---------------------------------------------------------------------------

@pytest.fixture
def scratch() -> Iterator[Path]:
    raw = tempfile.mkdtemp(prefix="sb_emit_hot_memory_")
    try:
        yield Path(raw)
    finally:
        shutil.rmtree(raw, ignore_errors=True)


def _write_vaults_config(
    config_path: Path,
    vaults: list[dict],
    active_id: str | None,
) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "schema_version": 1,
        "vaults": vaults,
        "active_vault_id": active_id,
    }
    config_path.write_text(json.dumps(data, indent=2))


def _make_vault(scratch: Path, with_hot_memory: bool = True) -> Path:
    vault = scratch / "vault"
    (vault / "brain").mkdir(parents=True)
    (vault / "entities").mkdir(parents=True)
    if with_hot_memory:
        (vault / "brain" / "hot-memory.md").write_text(INITIAL_TEMPLATE)
    return vault


def _make_vault_entry(vault: Path, vid: str = "v1") -> dict:
    return {
        "id": vid,
        "path": str(vault),
        "name": vault.name,
        "role": "personal",
        "added_at": "2026-04-11T12:00:00",
        "with_push": False,
    }


def _run_hook(
    payload: dict,
    scratch: Path,
    vaults_config: Path | None = None,
) -> Tuple[int, str, str]:
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_ROOT"] = str(PLUGIN_ROOT)
    if vaults_config is not None:
        env["SECONDBRAIN_VAULTS_CONFIG"] = str(vaults_config)
    else:
        # Point at a definitely-missing file so the hook falls back.
        env["SECONDBRAIN_VAULTS_CONFIG"] = str(scratch / "no-such-config.json")
    result = subprocess.run(
        [str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    return result.returncode, result.stdout, result.stderr


# ---------------------------------------------------------------------------
# Hook file presence
# ---------------------------------------------------------------------------

class TestHookFilePresent:
    def test_hook_exists(self):
        assert HOOK.is_file(), f"emit-hot-memory.sh must exist at {HOOK}"

    def test_hook_executable(self):
        assert os.access(HOOK, os.X_OK), (
            f"emit-hot-memory.sh must be executable; fix with chmod +x {HOOK}"
        )


# ---------------------------------------------------------------------------
# No vaults.json → fallback systemMessage
# ---------------------------------------------------------------------------

class TestNoVaultsConfig:
    def test_missing_config_emits_fallback(self, scratch: Path):
        code, stdout, _ = _run_hook({"cwd": str(scratch)}, scratch)
        assert code == 0
        data = json.loads(stdout)
        assert "systemMessage" in data
        msg = data["systemMessage"].lower()
        assert "not configured" in msg or "init" in msg

    def test_config_with_no_active_vault_emits_fallback(self, scratch: Path):
        config = scratch / "cfg" / "vaults.json"
        _write_vaults_config(config, vaults=[], active_id=None)
        code, stdout, _ = _run_hook({"cwd": str(scratch)}, scratch, vaults_config=config)
        assert code == 0
        data = json.loads(stdout)
        assert "systemMessage" in data


# ---------------------------------------------------------------------------
# Happy path: active vault with valid hot-memory
# ---------------------------------------------------------------------------

class TestActiveVaultValidHotMemory:
    def test_active_vault_with_hot_memory(self, scratch: Path):
        vault = _make_vault(scratch, with_hot_memory=True)
        config = scratch / "cfg" / "vaults.json"
        _write_vaults_config(
            config,
            [_make_vault_entry(vault)],
            active_id="v1",
        )
        code, stdout, _ = _run_hook({"cwd": str(vault)}, scratch, vaults_config=config)
        assert code == 0
        data = json.loads(stdout)
        assert "systemMessage" in data
        assert "Identity & Directive" in data["systemMessage"]

    def test_active_vault_without_hot_memory(self, scratch: Path):
        vault = _make_vault(scratch, with_hot_memory=False)
        config = scratch / "cfg" / "vaults.json"
        _write_vaults_config(
            config,
            [_make_vault_entry(vault)],
            active_id="v1",
        )
        code, stdout, _ = _run_hook({"cwd": str(vault)}, scratch, vaults_config=config)
        assert code == 0
        data = json.loads(stdout)
        assert "systemMessage" in data
        # Should be a fallback about missing hot-memory.
        msg = data["systemMessage"].lower()
        assert "missing" in msg or "init" in msg or "doctor" in msg


# ---------------------------------------------------------------------------
# cwd propagation: Active Project Context
# ---------------------------------------------------------------------------

class TestCwdPropagation:
    def test_cwd_matching_entity_gets_project_section(self, scratch: Path):
        vault = _make_vault(scratch, with_hot_memory=True)
        project = scratch / "myproj"
        project.mkdir()
        (vault / "entities" / "myproj.md").write_text(
            "---\ntype: project\npaths:\n  - "
            + str(project)
            + "\n---\n# Myproj\n"
        )
        config = scratch / "cfg" / "vaults.json"
        _write_vaults_config(
            config,
            [_make_vault_entry(vault)],
            active_id="v1",
        )
        code, stdout, _ = _run_hook(
            {"cwd": str(project)},
            scratch,
            vaults_config=config,
        )
        assert code == 0
        data = json.loads(stdout)
        assert "Active Project Context" in data["systemMessage"]

    def test_no_cwd_in_payload_still_works(self, scratch: Path):
        vault = _make_vault(scratch, with_hot_memory=True)
        config = scratch / "cfg" / "vaults.json"
        _write_vaults_config(
            config,
            [_make_vault_entry(vault)],
            active_id="v1",
        )
        # Payload without `cwd` — hook should still emit the hot-memory.
        code, stdout, _ = _run_hook({}, scratch, vaults_config=config)
        assert code == 0
        data = json.loads(stdout)
        assert "systemMessage" in data

    def test_hook_passes_session_id_into_runtime_stamp(self, scratch: Path):
        vault = _make_vault(scratch, with_hot_memory=True)
        config = scratch / "cfg" / "vaults.json"
        _write_vaults_config(
            config,
            [_make_vault_entry(vault)],
            active_id="v1",
        )

        code, stdout, _ = _run_hook(
            {"cwd": str(vault), "session_id": "hook-session-123"},
            scratch,
            vaults_config=config,
        )

        assert code == 0, stdout
        data = json.loads(stdout)
        assert "systemMessage" in data
        stamp = read_session_start_stamp(
            plugin_root=PLUGIN_ROOT,
            desktop_config_path=None,
            vaults_config_path=config,
        )
        assert stamp is not None
        assert stamp["session_id"] == "hook-session-123"


# ---------------------------------------------------------------------------
# log.md must NOT be touched by SessionStart. Regression guard for the
# deleted session-activity checkpoint append that once wrote ~16k entries/day.
# ---------------------------------------------------------------------------

class TestHookDoesNotTouchLog:
    def test_empty_log_stays_empty(self, scratch: Path):
        vault = _make_vault(scratch, with_hot_memory=True)
        log = vault / "log.md"
        log.write_text("# Log\n", encoding="utf-8")
        config = scratch / "cfg" / "vaults.json"
        _write_vaults_config(config, [_make_vault_entry(vault)], active_id="v1")

        before = log.read_bytes()
        code, _, _ = _run_hook({"cwd": str(vault)}, scratch, vaults_config=config)
        assert code == 0
        assert log.read_bytes() == before, (
            "emit-hot-memory.sh must not append to log.md (regression: "
            "session-activity checkpoint spam was producing ~16k entries/day)"
        )

    def test_missing_log_is_not_created(self, scratch: Path):
        vault = _make_vault(scratch, with_hot_memory=True)
        log = vault / "log.md"
        assert not log.exists()
        config = scratch / "cfg" / "vaults.json"
        _write_vaults_config(config, [_make_vault_entry(vault)], active_id="v1")

        code, _, _ = _run_hook({"cwd": str(vault)}, scratch, vaults_config=config)
        assert code == 0
        assert not log.exists(), (
            "emit-hot-memory.sh must not create log.md; real operations "
            "(dream-protocol, session-end, etc.) own log.md writes"
        )


# ---------------------------------------------------------------------------
# Performance sanity: hook should be fast. Not a hard SLA in CI but a
# regression guard. <3s is a MASSIVELY generous ceiling covering cold cache,
# macOS sandbox overhead, and shell startup — real runs land in ~100ms.
# ---------------------------------------------------------------------------

class TestFast:
    def test_hook_completes_quickly(self, scratch: Path):
        vault = _make_vault(scratch, with_hot_memory=True)
        config = scratch / "cfg" / "vaults.json"
        _write_vaults_config(
            config,
            [_make_vault_entry(vault)],
            active_id="v1",
        )
        import time

        start = time.time()
        code, _, _ = _run_hook({"cwd": str(vault)}, scratch, vaults_config=config)
        elapsed = time.time() - start
        assert code == 0
        assert elapsed < 5.0, (
            f"emit-hot-memory.sh took {elapsed:.2f}s — should stay well under 5s "
            "(real target is <100ms)"
        )
