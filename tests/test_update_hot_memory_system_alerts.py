"""Regression tests for the System Alerts leak (v3.6.2).

Before v3.6.2, `_build_system_alerts` called `resolve_vaults_config_path()`
which reads `SECONDBRAIN_VAULTS_CONFIG` from the ambient environment. When
the SessionStart hook spawned a detached `refresh_vault_indexes.py` process,
that child inherited the env var from a pytest subprocess. If the test's
scratch vaults.json was subsequently `rmtree`'d, the child saw `exists()=False`
and baked a pytest-tempdir path into the user's real `brain/hot-memory.md`:

    ## System Alerts
    - `vaults.json` missing at `/var/folders/.../T/sb_emit_hot_memory_5fcvqtn_/cfg/vaults.json` — ...

The fix threads `vaults_config` explicitly through the call chain. These
tests pin that contract: ambient env must never poison the alert.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "secondbrain" / "scripts"))

import update_hot_memory  # type: ignore[reportMissingImports]


class TestNoAlertWhenConfigExists:
    def test_returns_none_when_vaults_config_exists(self, tmp_path: Path):
        cfg = tmp_path / "vaults.json"
        cfg.write_text('{"schema_version": 1, "vaults": [], "active_vault_id": null}')
        result = update_hot_memory._build_system_alerts(
            vault_path=tmp_path, vaults_config=cfg
        )
        assert result is None, (
            "No alert expected when vaults.json exists — got: " + repr(result)
        )

    def test_returns_none_when_vaults_config_is_none(self, tmp_path: Path):
        # Explicit None means "don't check vaults.json at all". This is how
        # production threads the arg when the config check is irrelevant.
        result = update_hot_memory._build_system_alerts(
            vault_path=tmp_path, vaults_config=None
        )
        assert result is None


class TestAlertUsesSuppliedPath:
    def test_alert_references_supplied_path_not_env_var(
        self, tmp_path: Path, monkeypatch
    ):
        # An attacker could set SECONDBRAIN_VAULTS_CONFIG to a misleading
        # path; the new API must ignore it because the CLI resolves vaults
        # config explicitly. The supplied path is what appears in the alert.
        monkeypatch.setenv(
            "SECONDBRAIN_VAULTS_CONFIG", "/tmp/malicious-test-path/vaults.json"
        )
        supplied = tmp_path / "missing-cfg" / "vaults.json"
        assert not supplied.exists()

        result = update_hot_memory._build_system_alerts(
            vault_path=tmp_path, vaults_config=supplied
        )

        assert result is not None
        assert "vaults.json" in result
        assert str(supplied) in result, (
            "Alert should reference the explicitly-supplied path, not the "
            "env var. Got: " + result
        )
        assert "/tmp/malicious-test-path/vaults.json" not in result, (
            "Alert leaked the env-var path despite an explicit argument. "
            "This is the exact bug v3.6.2 fixes."
        )


class TestLegacyClaudeMdAlert:
    def test_detects_legacy_claude_md(self, tmp_path: Path):
        cfg = tmp_path / "vaults.json"
        cfg.write_text("{}")
        (tmp_path / "CLAUDE.md").write_text("# legacy\n")

        result = update_hot_memory._build_system_alerts(
            vault_path=tmp_path, vaults_config=cfg
        )
        assert result is not None
        assert "CLAUDE.md" in result

    def test_stays_silent_without_legacy_claude_md(self, tmp_path: Path):
        cfg = tmp_path / "vaults.json"
        cfg.write_text("{}")

        result = update_hot_memory._build_system_alerts(
            vault_path=tmp_path, vaults_config=cfg
        )
        assert result is None
