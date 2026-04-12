"""Schema contract tests for ~/.config/secondbrain/vaults.json.

These tests treat the on-disk config as an explicit schema contract that other
components (init, doctor, hooks, Phase 2 git remote work) depend on. Some
overlap with test_setup_steps.py is intentional: that file tests behavior of
the setup_steps module, this file tests invariants of the vaults.json schema
itself.

Current schema (schema_version=1):

    {
      "schema_version": 1,
      "vaults": [
        {
          "id": "<uuid>",
          "path": "<absolute path>",
          "name": "<display name>",
          "role": "personal" | "work" | "...",
          "added_at": "<ISO 8601>",
          "with_push": false
        }
      ],
      "active_vault_id": "<id>" | null
    }

`with_push` is forward-compat for Phase 2 (vault git remote with optional
auto-push). It is read/written through the config but unused today.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Iterator

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "secondbrain" / "scripts"))

from setup_steps import (  # type: ignore[reportMissingImports]
    add_vault_to_config,
    remove_vault_from_config,
    list_configured_vaults,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    config_path = tmp_path / "config" / "secondbrain" / "vaults.json"
    monkeypatch.setenv("SECONDBRAIN_VAULTS_CONFIG", str(config_path))
    yield config_path


# ---------------------------------------------------------------------------
# Schema invariants
# ---------------------------------------------------------------------------

class TestSchemaContract:
    def test_fresh_config_has_schema_version_1(self, isolated_config: Path, tmp_path: Path):
        vault = tmp_path / "vault"; vault.mkdir()
        add_vault_to_config(vault, "id-1", "Vault")
        data = json.loads(isolated_config.read_text())
        assert data["schema_version"] == 1
        assert isinstance(data["schema_version"], int)

    def test_empty_vaults_list_is_valid(self, isolated_config: Path, tmp_path: Path):
        # Add then remove — we're left with an empty list but a valid config.
        vault = tmp_path / "vault"; vault.mkdir()
        add_vault_to_config(vault, "id-1", "Vault")
        remove_vault_from_config("id-1")

        data = json.loads(isolated_config.read_text())
        assert data["schema_version"] == 1
        assert data["vaults"] == []
        assert data["active_vault_id"] is None

    def test_active_vault_id_can_be_null(self, isolated_config: Path):
        # Directly write a minimal config.
        isolated_config.parent.mkdir(parents=True, exist_ok=True)
        isolated_config.write_text(json.dumps({
            "schema_version": 1,
            "vaults": [],
            "active_vault_id": None,
        }))
        # list_configured_vaults should not choke on active_vault_id=null.
        vaults = list_configured_vaults()
        assert vaults == []

    def test_required_entry_fields(self, isolated_config: Path, tmp_path: Path):
        vault = tmp_path / "vault"; vault.mkdir()
        add_vault_to_config(vault, "id-1", "Vault", role="personal")
        data = json.loads(isolated_config.read_text())
        entry = data["vaults"][0]
        # Every field required by the schema must be present.
        for key in ("id", "path", "name", "role", "added_at", "with_push"):
            assert key in entry, f"schema missing field: {key}"
        # Field types
        assert isinstance(entry["id"], str)
        assert isinstance(entry["path"], str)
        assert isinstance(entry["name"], str)
        assert isinstance(entry["role"], str)
        assert isinstance(entry["added_at"], str)
        assert isinstance(entry["with_push"], bool)

    def test_multi_vault_entries_allowed(self, isolated_config: Path, tmp_path: Path):
        # Forward-compat: even though only one vault is used today, the schema
        # must tolerate multiple entries. Phase 3 will activate this fully.
        v1 = tmp_path / "v1"; v1.mkdir()
        v2 = tmp_path / "v2"; v2.mkdir()
        v3 = tmp_path / "v3"; v3.mkdir()
        add_vault_to_config(v1, "id-1", "V1")
        add_vault_to_config(v2, "id-2", "V2", role="work")
        add_vault_to_config(v3, "id-3", "V3")

        data = json.loads(isolated_config.read_text())
        assert len(data["vaults"]) == 3
        # Still valid JSON, still resilient to list_configured_vaults.
        vaults = list_configured_vaults()
        assert len(vaults) == 3


# ---------------------------------------------------------------------------
# JSON stays valid under arbitrary add/remove sequences
# ---------------------------------------------------------------------------

class TestJsonInvariants:
    def test_json_valid_after_every_step(self, isolated_config: Path, tmp_path: Path):
        v1 = tmp_path / "v1"; v1.mkdir()
        v2 = tmp_path / "v2"; v2.mkdir()
        v3 = tmp_path / "v3"; v3.mkdir()

        ops = [
            ("add", v1, "id-1", "First"),
            ("add", v2, "id-2", "Second"),
            ("remove", None, "id-1", None),
            ("add", v3, "id-3", "Third"),
            ("add", v1, "id-1", "First Again"),
            ("remove", None, "id-3", None),
            ("add", v2, "id-2", "Renamed"),
            ("remove", None, "id-2", None),
            ("remove", None, "id-1", None),
        ]

        for op in ops:
            kind = op[0]
            if kind == "add":
                _, path, vid, name = op
                add_vault_to_config(path, vid, name)
            else:
                _, _, vid, _ = op
                remove_vault_from_config(vid)
            # After each step, file must parse as valid JSON with the schema shape.
            data = json.loads(isolated_config.read_text())
            assert data["schema_version"] == 1
            assert isinstance(data["vaults"], list)
            assert "active_vault_id" in data
            # Every vault entry in the list must still be schema-conformant.
            for entry in data["vaults"]:
                for key in ("id", "path", "name", "role", "added_at", "with_push"):
                    assert key in entry

        # Final state: empty.
        data = json.loads(isolated_config.read_text())
        assert data["vaults"] == []
        assert data["active_vault_id"] is None

    def test_json_stable_on_idempotent_readds(self, isolated_config: Path, tmp_path: Path):
        v1 = tmp_path / "v1"; v1.mkdir()
        add_vault_to_config(v1, "id-1", "V1")
        first = isolated_config.read_text()

        add_vault_to_config(v1, "id-1", "V1")  # no-op
        second = isolated_config.read_text()
        assert first == second
