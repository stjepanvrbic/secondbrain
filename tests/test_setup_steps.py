"""Tests for setup_steps.py — shared setup-step primitives used by init + doctor.

The module owns three concerns, and tests mirror that split:

1. `StepResult` + `VaultEntry` dataclasses (construction / defaults)
2. Per-vault filesystem side-effects (marker file, shell config)
3. `~/.config/secondbrain/vaults.json` multi-vault registry

All tests isolate filesystem state via `tmp_path` and
`monkeypatch.setenv("SECONDBRAIN_VAULTS_CONFIG", ...)` so the real user's
config is never touched.
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

from setup_steps import (  # type: ignore[reportMissingImports]
    StepResult,
    VaultEntry,
    VAULTS_CONFIG_PATH,
    detect_environment,
    detect_obsidian,
    setup_env_vars,
    write_vault_id,
    add_vault_to_config,
    remove_vault_from_config,
    list_configured_vaults,
    get_active_vault,
    list_vault_paths_for_hooks,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Redirect VAULTS_CONFIG_PATH into tmp_path via the override env var."""
    config_path = tmp_path / "config" / "secondbrain" / "vaults.json"
    monkeypatch.setenv("SECONDBRAIN_VAULTS_CONFIG", str(config_path))
    yield config_path


@pytest.fixture
def vault_with_marker(tmp_path: Path) -> Path:
    """A vault path with a pre-seeded .secondbrain-installed marker (no vault_id)."""
    vault = tmp_path / "vault"
    vault.mkdir()
    marker = vault / ".secondbrain-installed"
    marker.write_text(json.dumps({"steps": ["scaffold: ok"], "errors": []}, indent=2))
    return vault


# ---------------------------------------------------------------------------
# StepResult / VaultEntry dataclasses
# ---------------------------------------------------------------------------

class TestStepResult:
    def test_basic_construction(self):
        r = StepResult(success=True, message="ok", did_work=False)
        assert r.success is True
        assert r.message == "ok"
        assert r.did_work is False
        assert r.error is None

    def test_with_error(self):
        r = StepResult(success=False, message="failed", did_work=False, error="boom")
        assert r.success is False
        assert r.error == "boom"

    def test_error_defaults_to_none(self):
        r = StepResult(success=True, message="ok", did_work=True)
        assert r.error is None


class TestVaultEntry:
    def test_construction_with_defaults(self):
        e = VaultEntry(
            id="abc-123",
            path="/home/user/vault",
            name="My Vault",
            role="personal",
            added_at="2026-04-11T10:00:00",
        )
        assert e.id == "abc-123"
        assert e.with_push is False  # defaults to False

    def test_with_push_override(self):
        e = VaultEntry(
            id="abc-123",
            path="/home/user/vault",
            name="Vault",
            role="personal",
            added_at="2026-04-11T10:00:00",
            with_push=True,
        )
        assert e.with_push is True


# ---------------------------------------------------------------------------
# VAULTS_CONFIG_PATH constant + env override
# ---------------------------------------------------------------------------

class TestVaultsConfigPath:
    def test_default_location(self):
        # Without the env var, VAULTS_CONFIG_PATH points at ~/.config/secondbrain/vaults.json.
        # We test this by checking the module-level constant directly.
        assert VAULTS_CONFIG_PATH == Path.home() / ".config" / "secondbrain" / "vaults.json"


# ---------------------------------------------------------------------------
# detect_environment / detect_obsidian (thin wrappers)
# ---------------------------------------------------------------------------

class TestDetectEnvironment:
    def test_returns_valid_string(self):
        env = detect_environment()
        assert env in ("code", "cowork")

    def test_returns_plain_string_not_stepresult(self):
        env = detect_environment()
        assert isinstance(env, str)


class TestDetectObsidian:
    def test_returns_path_or_none(self):
        result = detect_obsidian()
        assert result is None or isinstance(result, Path)


# ---------------------------------------------------------------------------
# write_vault_id — idempotent UUID into .secondbrain-installed
# ---------------------------------------------------------------------------

class TestWriteVaultId:
    def test_fails_when_marker_missing(self, tmp_path: Path):
        vault = tmp_path / "no-marker"
        vault.mkdir()
        result = write_vault_id(vault)
        assert result.success is False
        assert result.did_work is False
        assert "marker" in result.message.lower() or "marker" in (result.error or "").lower()

    def test_adds_vault_id_when_missing(self, vault_with_marker: Path):
        result = write_vault_id(vault_with_marker)
        assert result.success is True
        assert result.did_work is True
        # UUID should appear in the message
        assert "vault_id=" in result.message
        # Marker JSON now has vault_id
        data = json.loads((vault_with_marker / ".secondbrain-installed").read_text())
        assert "vault_id" in data
        # Validate UUID4 shape
        parsed = uuid.UUID(data["vault_id"])
        assert parsed.version == 4

    def test_preserves_existing_vault_id(self, vault_with_marker: Path):
        existing_id = "11111111-2222-4333-8444-555555555555"
        marker = vault_with_marker / ".secondbrain-installed"
        data = json.loads(marker.read_text())
        data["vault_id"] = existing_id
        marker.write_text(json.dumps(data, indent=2))

        result = write_vault_id(vault_with_marker)
        assert result.success is True
        assert result.did_work is False
        assert existing_id in result.message
        # File content preserved
        final = json.loads(marker.read_text())
        assert final["vault_id"] == existing_id

    def test_idempotent_on_rerun(self, vault_with_marker: Path):
        first = write_vault_id(vault_with_marker)
        assert first.did_work is True
        first_id = json.loads((vault_with_marker / ".secondbrain-installed").read_text())["vault_id"]

        second = write_vault_id(vault_with_marker)
        assert second.success is True
        assert second.did_work is False

        second_id = json.loads((vault_with_marker / ".secondbrain-installed").read_text())["vault_id"]
        assert second_id == first_id

    def test_marker_json_remains_valid(self, vault_with_marker: Path):
        result = write_vault_id(vault_with_marker)
        assert result.success
        raw = (vault_with_marker / ".secondbrain-installed").read_text()
        data = json.loads(raw)  # must parse without raising
        # Original fields preserved
        assert "steps" in data
        assert "errors" in data

    def test_generated_uuid_is_uuid4(self, vault_with_marker: Path):
        write_vault_id(vault_with_marker)
        data = json.loads((vault_with_marker / ".secondbrain-installed").read_text())
        parsed = uuid.UUID(data["vault_id"])
        assert parsed.version == 4
        # Also a string, not bytes
        assert isinstance(data["vault_id"], str)


# ---------------------------------------------------------------------------
# add_vault_to_config
# ---------------------------------------------------------------------------

class TestAddVaultToConfig:
    def test_creates_file_and_parent_dir(self, isolated_config: Path, tmp_path: Path):
        vault = tmp_path / "vault1"
        vault.mkdir()
        result = add_vault_to_config(vault, "id-1", "Vault One")
        assert result.success is True
        assert result.did_work is True
        assert isolated_config.exists()
        assert isolated_config.parent.exists()

    def test_writes_schema(self, isolated_config: Path, tmp_path: Path):
        vault = tmp_path / "vault1"
        vault.mkdir()
        add_vault_to_config(vault, "id-1", "Vault One")
        data = json.loads(isolated_config.read_text())
        assert data["schema_version"] == 1
        assert isinstance(data["vaults"], list)
        assert len(data["vaults"]) == 1
        entry = data["vaults"][0]
        assert entry["id"] == "id-1"
        assert entry["name"] == "Vault One"
        assert entry["role"] == "personal"
        assert "added_at" in entry
        assert "with_push" in entry

    @pytest.mark.usefixtures("isolated_config")
    def test_idempotent_no_changes(self, tmp_path: Path):
        vault = tmp_path / "vault1"
        vault.mkdir()
        first = add_vault_to_config(vault, "id-1", "Vault One")
        assert first.did_work is True

        second = add_vault_to_config(vault, "id-1", "Vault One")
        assert second.success is True
        assert second.did_work is False

    def test_updates_in_place_when_fields_change(self, isolated_config: Path, tmp_path: Path):
        vault = tmp_path / "vault1"
        vault.mkdir()
        add_vault_to_config(vault, "id-1", "Old Name")

        result = add_vault_to_config(vault, "id-1", "New Name")
        assert result.did_work is True
        data = json.loads(isolated_config.read_text())
        assert len(data["vaults"]) == 1  # no duplicate
        assert data["vaults"][0]["name"] == "New Name"

    def test_sets_active_if_unset(self, isolated_config: Path, tmp_path: Path):
        vault = tmp_path / "vault1"
        vault.mkdir()
        add_vault_to_config(vault, "id-1", "Vault One")
        data = json.loads(isolated_config.read_text())
        assert data["active_vault_id"] == "id-1"

    def test_does_not_clobber_active_when_set(self, isolated_config: Path, tmp_path: Path):
        v1 = tmp_path / "vault1"; v1.mkdir()
        v2 = tmp_path / "vault2"; v2.mkdir()
        add_vault_to_config(v1, "id-1", "First")
        # Active is now id-1.
        add_vault_to_config(v2, "id-2", "Second")
        data = json.loads(isolated_config.read_text())
        assert data["active_vault_id"] == "id-1"
        assert len(data["vaults"]) == 2

    def test_custom_role(self, isolated_config: Path, tmp_path: Path):
        vault = tmp_path / "vault1"; vault.mkdir()
        add_vault_to_config(vault, "id-1", "Work Vault", role="work")
        data = json.loads(isolated_config.read_text())
        assert data["vaults"][0]["role"] == "work"


# ---------------------------------------------------------------------------
# add_vault_to_config(with_push=...) — T8 extension
# ---------------------------------------------------------------------------
#
# Phase 2 needs a per-vault flag recording whether the user opted into
# auto-push so the Stop hook (T9) can decide whether to push after every
# commit. The flag lives in vaults.json and is set through add_vault_to_config
# — we do NOT expose a separate update_vault_config function because the
# semantics are identical (add-or-update a single entry).

class TestAddVaultToConfigWithPush:
    def test_fresh_add_defaults_with_push_false(
        self, isolated_config: Path, tmp_path: Path
    ):
        vault = tmp_path / "vault1"; vault.mkdir()
        add_vault_to_config(vault, "id-1", "Vault One")
        data = json.loads(isolated_config.read_text())
        assert data["vaults"][0]["with_push"] is False

    def test_fresh_add_with_push_true(
        self, isolated_config: Path, tmp_path: Path
    ):
        vault = tmp_path / "vault1"; vault.mkdir()
        result = add_vault_to_config(
            vault, "id-1", "Vault One", with_push=True
        )
        assert result.success is True
        assert result.did_work is True
        data = json.loads(isolated_config.read_text())
        assert data["vaults"][0]["with_push"] is True

    def test_update_flips_with_push_counts_as_did_work(
        self, isolated_config: Path, tmp_path: Path
    ):
        vault = tmp_path / "vault1"; vault.mkdir()
        add_vault_to_config(vault, "id-1", "Vault One", with_push=False)

        # Flip with_push from False to True — this is a real change.
        result = add_vault_to_config(
            vault, "id-1", "Vault One", with_push=True
        )
        assert result.success is True
        assert result.did_work is True

        data = json.loads(isolated_config.read_text())
        assert data["vaults"][0]["with_push"] is True

    def test_readd_same_with_push_is_noop(
        self, isolated_config: Path, tmp_path: Path
    ):
        vault = tmp_path / "vault1"; vault.mkdir()
        add_vault_to_config(vault, "id-1", "Vault One", with_push=True)

        result = add_vault_to_config(
            vault, "id-1", "Vault One", with_push=True
        )
        assert result.success is True
        assert result.did_work is False

    def test_role_can_combine_with_with_push(
        self, isolated_config: Path, tmp_path: Path
    ):
        vault = tmp_path / "vault1"; vault.mkdir()
        add_vault_to_config(
            vault, "id-1", "Work Vault", role="work", with_push=True
        )
        data = json.loads(isolated_config.read_text())
        entry = data["vaults"][0]
        assert entry["role"] == "work"
        assert entry["with_push"] is True


# ---------------------------------------------------------------------------
# remove_vault_from_config
# ---------------------------------------------------------------------------

class TestRemoveVaultFromConfig:
    def test_removes_entry(self, isolated_config: Path, tmp_path: Path):
        vault = tmp_path / "vault1"; vault.mkdir()
        add_vault_to_config(vault, "id-1", "Vault One")

        result = remove_vault_from_config("id-1")
        assert result.success is True
        assert result.did_work is True

        data = json.loads(isolated_config.read_text())
        assert data["vaults"] == []

    def test_clears_active_when_removed(self, isolated_config: Path, tmp_path: Path):
        v1 = tmp_path / "vault1"; v1.mkdir()
        add_vault_to_config(v1, "id-1", "First")
        remove_vault_from_config("id-1")
        data = json.loads(isolated_config.read_text())
        assert data["active_vault_id"] is None

    def test_falls_back_active_to_first_remaining(self, isolated_config: Path, tmp_path: Path):
        v1 = tmp_path / "vault1"; v1.mkdir()
        v2 = tmp_path / "vault2"; v2.mkdir()
        add_vault_to_config(v1, "id-1", "First")
        add_vault_to_config(v2, "id-2", "Second")
        # Active is still id-1. Remove it — id-2 should become active.
        remove_vault_from_config("id-1")
        data = json.loads(isolated_config.read_text())
        assert data["active_vault_id"] == "id-2"
        assert len(data["vaults"]) == 1

    def test_idempotent_nonexistent(self, isolated_config: Path, tmp_path: Path):
        v1 = tmp_path / "vault1"; v1.mkdir()
        add_vault_to_config(v1, "id-1", "First")

        result = remove_vault_from_config("nope")
        assert result.success is True
        assert result.did_work is False
        # Original vault still there
        data = json.loads(isolated_config.read_text())
        assert len(data["vaults"]) == 1

    def test_removing_from_missing_config(self, isolated_config: Path):
        # Config file never created
        assert not isolated_config.exists()
        result = remove_vault_from_config("id-1")
        assert result.success is True
        assert result.did_work is False


# ---------------------------------------------------------------------------
# list_configured_vaults / get_active_vault
# ---------------------------------------------------------------------------

class TestListConfiguredVaults:
    def test_empty_when_file_missing(self, isolated_config: Path):
        assert not isolated_config.exists()
        vaults = list_configured_vaults()
        assert vaults == []

    @pytest.mark.usefixtures("isolated_config")
    def test_returns_entries(self, tmp_path: Path):
        v1 = tmp_path / "vault1"; v1.mkdir()
        v2 = tmp_path / "vault2"; v2.mkdir()
        add_vault_to_config(v1, "id-1", "First")
        add_vault_to_config(v2, "id-2", "Second")

        vaults = list_configured_vaults()
        assert len(vaults) == 2
        assert all(isinstance(v, VaultEntry) for v in vaults)
        ids = {v.id for v in vaults}
        assert ids == {"id-1", "id-2"}

    def test_raises_on_malformed(self, isolated_config: Path):
        isolated_config.parent.mkdir(parents=True, exist_ok=True)
        isolated_config.write_text("{this is not valid json")
        with pytest.raises((json.JSONDecodeError, ValueError)):
            list_configured_vaults()


class TestGetActiveVault:
    @pytest.mark.usefixtures("isolated_config")
    def test_none_when_no_config(self):
        assert get_active_vault() is None

    def test_none_when_active_unset(self, isolated_config: Path):
        # Write schema directly with empty vaults list + null active.
        isolated_config.parent.mkdir(parents=True, exist_ok=True)
        isolated_config.write_text(json.dumps({
            "schema_version": 1,
            "vaults": [],
            "active_vault_id": None,
        }))
        assert get_active_vault() is None

    @pytest.mark.usefixtures("isolated_config")
    def test_returns_active_entry(self, tmp_path: Path):
        v1 = tmp_path / "vault1"; v1.mkdir()
        add_vault_to_config(v1, "id-1", "First")
        active = get_active_vault()
        assert active is not None
        assert active.id == "id-1"
        assert active.name == "First"


# ---------------------------------------------------------------------------
# list_vault_paths_for_hooks
# ---------------------------------------------------------------------------

class TestListVaultPathsForHooks:
    @pytest.mark.usefixtures("isolated_config")
    def test_empty_when_pre_init(self):
        assert list_vault_paths_for_hooks() == []

    @pytest.mark.usefixtures("isolated_config")
    def test_returns_absolute_paths(self, tmp_path: Path):
        v1 = tmp_path / "vault1"; v1.mkdir()
        v2 = tmp_path / "vault2"; v2.mkdir()
        add_vault_to_config(v1, "id-1", "First")
        add_vault_to_config(v2, "id-2", "Second")

        paths = list_vault_paths_for_hooks()
        assert len(paths) == 2
        for p in paths:
            assert isinstance(p, str)
            assert os.path.isabs(p)
        assert str(v1) in paths
        assert str(v2) in paths


# ---------------------------------------------------------------------------
# setup_env_vars — wraps init_obsidian.set_env_vars in a StepResult
# ---------------------------------------------------------------------------

class TestSetupEnvVars:
    def test_adds_exports_on_fresh_config(self, tmp_path: Path):
        shell_config = tmp_path / ".zshrc"
        shell_config.write_text("# existing content\n")
        result = setup_env_vars("test-key", 27124, shell_path=shell_config)
        assert result.success is True
        assert result.did_work is True
        content = shell_config.read_text()
        assert "OBSIDIAN_API_KEY" in content
        assert "OBSIDIAN_MCP_PORT" in content
        assert "test-key" in content
        assert "27124" in content

    def test_idempotent_on_rerun(self, tmp_path: Path):
        shell_config = tmp_path / ".zshrc"
        shell_config.write_text("# existing content\n")
        first = setup_env_vars("test-key", 27124, shell_path=shell_config)
        assert first.did_work is True

        second = setup_env_vars("test-key", 27124, shell_path=shell_config)
        assert second.success is True
        assert second.did_work is False

        content = shell_config.read_text()
        # Count should be exactly 1 for each export line
        assert content.count("OBSIDIAN_MCP_PORT") == 1
        assert content.count("OBSIDIAN_API_KEY") == 1

    def test_dry_run_makes_no_changes(self, tmp_path: Path):
        shell_config = tmp_path / ".zshrc"
        shell_config.write_text("# existing content\n")
        original = shell_config.read_text()
        result = setup_env_vars("test-key", 27124, shell_path=shell_config, dry_run=True)
        assert result.success is True
        # Content untouched.
        assert shell_config.read_text() == original

    def test_missing_api_key_still_writes_port(self, tmp_path: Path):
        shell_config = tmp_path / ".zshrc"
        shell_config.write_text("")
        result = setup_env_vars(None, 27124, shell_path=shell_config)
        assert result.success is True
        content = shell_config.read_text()
        assert "OBSIDIAN_MCP_PORT" in content
        assert "27124" in content
        # API key NOT written when not provided
        assert "OBSIDIAN_API_KEY" not in content

    def test_missing_port_still_writes_api_key(self, tmp_path: Path):
        shell_config = tmp_path / ".zshrc"
        shell_config.write_text("")
        result = setup_env_vars("test-key", None, shell_path=shell_config)
        assert result.success is True
        content = shell_config.read_text()
        assert "OBSIDIAN_API_KEY" in content
        assert "test-key" in content

    def test_windows_powershell_branch(self, monkeypatch: pytest.MonkeyPatch):
        """On Windows, setup_env_vars must delegate to init_obsidian._set_env_vars_powershell
        rather than failing with a 'no config mapping for shell powershell' error.

        Pre-fix regression: setup_env_vars only knew about POSIX shell configs
        (zsh/bash/fish via SHELL_CONFIGS), so when detect_shell() returned
        'powershell' on Windows the lookup in SHELL_CONFIGS returned None and
        the function reported success=False.
        """
        import init_obsidian  # type: ignore[reportMissingImports]

        monkeypatch.setattr(init_obsidian, "detect_shell", lambda: "powershell")

        calls: list[tuple] = []

        def fake_powershell(port: int, api_key, dry_run: bool = False) -> bool:
            calls.append((port, api_key, dry_run))
            return True

        monkeypatch.setattr(init_obsidian, "_set_env_vars_powershell", fake_powershell)

        result = setup_env_vars(api_key="test-key", port=27124, dry_run=True)

        assert result.success is True, f"expected success but got: {result}"
        assert result.error is None
        # Verify the call path went through the powershell branch with correct args.
        assert calls == [(27124, "test-key", True)]

    def test_explicit_shell_path_stays_on_posix_branch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """When shell_path is passed explicitly (the test convention), setup_env_vars
        must honor it and use the POSIX path even if detect_shell would say powershell.
        This guards against the fix accidentally hijacking the explicit-path case.
        """
        import init_obsidian  # type: ignore[reportMissingImports]

        monkeypatch.setattr(init_obsidian, "detect_shell", lambda: "powershell")

        called = []

        def should_not_be_called(*args, **kwargs):
            called.append((args, kwargs))
            return True

        monkeypatch.setattr(init_obsidian, "_set_env_vars_powershell", should_not_be_called)

        shell_config = tmp_path / ".zshrc"
        shell_config.write_text("")
        result = setup_env_vars("test-key", 27124, shell_path=shell_config)

        assert result.success is True
        assert called == []  # powershell branch NOT taken
        content = shell_config.read_text()
        assert "OBSIDIAN_API_KEY" in content
        assert "OBSIDIAN_MCP_PORT" in content


# ---------------------------------------------------------------------------
# Import hygiene — module must be importable without side effects
# ---------------------------------------------------------------------------

class TestImportHygiene:
    def test_importable_without_side_effects(self, tmp_path: Path):
        """Importing setup_steps in a fresh process should not touch the filesystem."""
        # Point the override at a location that must NOT be created by import.
        probe = tmp_path / "should-not-exist" / "vaults.json"
        scripts_dir = Path(__file__).resolve().parent.parent / "secondbrain" / "scripts"
        env = {**os.environ, "SECONDBRAIN_VAULTS_CONFIG": str(probe)}
        result = subprocess.run(
            [sys.executable, "-c", "import setup_steps"],
            cwd=str(scripts_dir),
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"import failed: {result.stderr}"
        assert not probe.exists()
        assert not probe.parent.exists()
