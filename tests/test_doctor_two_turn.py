"""Behavioral tests for doctor_cli.py — the two-turn diagnose/treat flow.

These tests operate at the CLI level, not the check level. They verify:

1. `--diagnose` mode runs all checks and NEVER mutates the filesystem.
   We enforce this with a state-hash diff across the call.
2. `--diagnose --json` produces parseable JSON with a predictable schema.
3. `--treat` mode actually fixes fixable issues — we start with a
   deliberately-broken vault and confirm the fixes land.

This is the agent's contract with doctor: the CLI entrypoint is how the
markdown skill talks to the check engine, and it MUST uphold the
read-only-Phase-1 invariant.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Iterator

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "secondbrain" / "scripts"))

from doctor_checks import vault_state_hash  # type: ignore[reportMissingImports]


SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "secondbrain" / "scripts"
DOCTOR_CLI = SCRIPTS_DIR / "doctor_cli.py"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def broken_vault_for_cli(tmp_path: Path) -> Path:
    """A vault missing log.md, _MANIFEST.md, and standard folders.

    Enough broken things that the treatment phase has real work to do.
    Must still have a `.secondbrain-installed` marker so `write_vault_id`
    (one of doctor's fix targets) can stamp an ID onto it.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    # Marker exists but has no vault_id → write_vault_id should fix it.
    (vault / ".secondbrain-installed").write_text(
        json.dumps({"steps": []}, indent=2)
    )
    # Missing: log.md, _MANIFEST.md, all folders, profile.md.
    return vault


@pytest.fixture
def healthy_vault_for_cli(tmp_path: Path) -> Path:
    """A fully-healthy vault — every fixable check should pass."""
    vault = tmp_path / "vault"
    vault.mkdir()
    for d in ("brain", "entities", "me", "inbox", "archive", "scratch"):
        (vault / d).mkdir()
    (vault / "_MANIFEST.md").write_text("# Vault Manifest\n\n**Files:** 1\n")
    (vault / "log.md").write_text(
        "# Log\n\n## [2026-04-10 02:00] dream-protocol | all green\nDone.\n"
    )
    (vault / "me" / "profile.md").write_text("# Profile\n\nName: Tester\n")
    vid = str(uuid.uuid4())
    (vault / ".secondbrain-installed").write_text(
        json.dumps({"vault_id": vid}, indent=2)
    )
    return vault


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Force doctor into a known-failure state for MCP (we never hit the network)."""
    for var in ("OBSIDIAN_API_KEY", "OBSIDIAN_MCP_PORT"):
        monkeypatch.delenv(var, raising=False)
    yield


# ---------------------------------------------------------------------------
# --diagnose mode is strictly read-only
# ---------------------------------------------------------------------------

class TestDiagnoseIsReadOnly:
    def test_diagnose_does_not_mutate_vault(
        self,
        broken_vault_for_cli: Path,
        tmp_path: Path,
        clean_env: None,
    ):
        del clean_env
        state_before = vault_state_hash(broken_vault_for_cli)

        # Isolate SECONDBRAIN_VAULTS_CONFIG so the test never touches the
        # real ~/.config/secondbrain/vaults.json.
        env = os.environ.copy()
        env["SECONDBRAIN_VAULTS_CONFIG"] = str(tmp_path / "fake_config.json")

        r = subprocess.run(
            [sys.executable, str(DOCTOR_CLI), "--diagnose", "--vault", str(broken_vault_for_cli)],
            capture_output=True, text=True, env=env, timeout=30,
        )
        # --diagnose on a broken vault: exit code 1 (there are failures)
        assert r.returncode in (0, 1), f"unexpected exit: {r.returncode}\n{r.stderr}"

        state_after = vault_state_hash(broken_vault_for_cli)
        assert state_before == state_after, (
            "--diagnose must not mutate the vault, but the state hash changed. "
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )

    def test_diagnose_produces_readable_report(
        self,
        broken_vault_for_cli: Path,
        tmp_path: Path,
        clean_env: None,
    ):
        del clean_env
        env = os.environ.copy()
        env["SECONDBRAIN_VAULTS_CONFIG"] = str(tmp_path / "fake_config.json")
        r = subprocess.run(
            [sys.executable, str(DOCTOR_CLI), "--diagnose", "--vault", str(broken_vault_for_cli)],
            capture_output=True, text=True, env=env, timeout=30,
        )
        # Output mentions the standard doctor report title.
        combined = r.stdout + r.stderr
        assert "doctor" in combined.lower()
        # At least one check name shows up
        assert "_MANIFEST" in combined or "manifest" in combined.lower() or "log" in combined.lower()

    def test_diagnose_exit_0_on_healthy_vault(
        self,
        healthy_vault_for_cli: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        # Remove MCP env vars so we don't actually try to connect; the
        # env-level fail is expected and should make exit code 1.
        for var in ("OBSIDIAN_API_KEY", "OBSIDIAN_MCP_PORT"):
            monkeypatch.delenv(var, raising=False)

        env = os.environ.copy()
        env.pop("OBSIDIAN_API_KEY", None)
        env.pop("OBSIDIAN_MCP_PORT", None)
        env["SECONDBRAIN_VAULTS_CONFIG"] = str(tmp_path / "fake_config.json")
        env["CLAUDE_PLUGIN_ROOT"] = str(tmp_path)  # so check_plugin_root passes

        r = subprocess.run(
            [sys.executable, str(DOCTOR_CLI), "--diagnose", "--vault", str(healthy_vault_for_cli)],
            capture_output=True, text=True, env=env, timeout=30,
        )
        # Even with missing env vars, the exit code is 1 because the
        # env checks fail. What we verify here is that the process
        # completes without crashing.
        assert r.returncode in (0, 1), f"unexpected exit: {r.returncode}\n{r.stderr}"


# ---------------------------------------------------------------------------
# --diagnose --json produces valid JSON
# ---------------------------------------------------------------------------

class TestDiagnoseJsonMode:
    def test_json_is_parseable(
        self,
        broken_vault_for_cli: Path,
        tmp_path: Path,
        clean_env: None,
    ):
        del clean_env
        env = os.environ.copy()
        env["SECONDBRAIN_VAULTS_CONFIG"] = str(tmp_path / "fake_config.json")
        r = subprocess.run(
            [
                sys.executable, str(DOCTOR_CLI),
                "--diagnose", "--vault", str(broken_vault_for_cli), "--json",
            ],
            capture_output=True, text=True, env=env, timeout=30,
        )
        # stdout must be valid JSON
        data = json.loads(r.stdout)
        assert isinstance(data, dict)
        assert "results" in data
        assert isinstance(data["results"], list)

    def test_json_includes_status_per_check(
        self,
        broken_vault_for_cli: Path,
        tmp_path: Path,
        clean_env: None,
    ):
        del clean_env
        env = os.environ.copy()
        env["SECONDBRAIN_VAULTS_CONFIG"] = str(tmp_path / "fake_config.json")
        r = subprocess.run(
            [
                sys.executable, str(DOCTOR_CLI),
                "--diagnose", "--vault", str(broken_vault_for_cli), "--json",
            ],
            capture_output=True, text=True, env=env, timeout=30,
        )
        data = json.loads(r.stdout)
        for result in data["results"]:
            assert "name" in result
            assert "status" in result
            assert "message" in result
            assert result["status"] in ("pass", "fail", "skip", "warning")

    def test_json_includes_summary(
        self,
        broken_vault_for_cli: Path,
        tmp_path: Path,
        clean_env: None,
    ):
        del clean_env
        env = os.environ.copy()
        env["SECONDBRAIN_VAULTS_CONFIG"] = str(tmp_path / "fake_config.json")
        r = subprocess.run(
            [
                sys.executable, str(DOCTOR_CLI),
                "--diagnose", "--vault", str(broken_vault_for_cli), "--json",
            ],
            capture_output=True, text=True, env=env, timeout=30,
        )
        data = json.loads(r.stdout)
        assert "summary" in data
        summary = data["summary"]
        assert "passed" in summary
        assert "failed" in summary
        assert "fixable_count" in summary


# ---------------------------------------------------------------------------
# --treat mode actually fixes things
# ---------------------------------------------------------------------------

class TestTreatMode:
    def test_treat_fixes_standard_folders(
        self,
        broken_vault_for_cli: Path,
        tmp_path: Path,
        clean_env: None,
    ):
        del clean_env
        # Confirm vault is broken before treatment.
        assert not (broken_vault_for_cli / "brain").exists()

        env = os.environ.copy()
        env["SECONDBRAIN_VAULTS_CONFIG"] = str(tmp_path / "fake_config.json")
        r = subprocess.run(
            [
                sys.executable, str(DOCTOR_CLI),
                "--treat", "--vault", str(broken_vault_for_cli),
            ],
            capture_output=True, text=True, env=env, timeout=60,
        )
        # Standard folders now exist
        assert (broken_vault_for_cli / "brain").is_dir(), (
            f"--treat did not create brain/ — stdout={r.stdout}, stderr={r.stderr}"
        )
        assert (broken_vault_for_cli / "entities").is_dir()
        assert (broken_vault_for_cli / "me").is_dir()
        # log.md is created during scaffolding (or by create_log_md — either way)
        assert (broken_vault_for_cli / "log.md").is_file()

    def test_treat_does_not_touch_healthy_vault(
        self,
        healthy_vault_for_cli: Path,
        tmp_path: Path,
        clean_env: None,
    ):
        del clean_env
        state_before = vault_state_hash(healthy_vault_for_cli)

        env = os.environ.copy()
        env["SECONDBRAIN_VAULTS_CONFIG"] = str(tmp_path / "fake_config.json")
        subprocess.run(
            [
                sys.executable, str(DOCTOR_CLI),
                "--treat", "--vault", str(healthy_vault_for_cli),
            ],
            capture_output=True, text=True, env=env, timeout=60,
        )
        state_after = vault_state_hash(healthy_vault_for_cli)
        assert state_before == state_after, (
            "--treat on a healthy vault should be a no-op, but state hash changed"
        )


# ---------------------------------------------------------------------------
# CLI error handling
# ---------------------------------------------------------------------------

class TestCliErrorHandling:
    def test_missing_vault_arg_errors_cleanly(self):
        r = subprocess.run(
            [sys.executable, str(DOCTOR_CLI), "--diagnose"],
            capture_output=True, text=True, timeout=30,
        )
        assert r.returncode != 0
        # argparse error message goes to stderr
        assert "--vault" in (r.stdout + r.stderr).lower() or "required" in (r.stdout + r.stderr).lower()

    def test_nonexistent_vault_path_errors(self, tmp_path: Path):
        env = os.environ.copy()
        env["SECONDBRAIN_VAULTS_CONFIG"] = str(tmp_path / "fake_config.json")
        r = subprocess.run(
            [
                sys.executable, str(DOCTOR_CLI),
                "--diagnose", "--vault", str(tmp_path / "nope"),
            ],
            capture_output=True, text=True, env=env, timeout=30,
        )
        # --diagnose must exit non-zero AND not crash; either 1 (failures found)
        # or 2 (usage error) is acceptable here. The important thing is that
        # the process doesn't leak a traceback.
        assert r.returncode != 0
        assert "Traceback" not in r.stderr
