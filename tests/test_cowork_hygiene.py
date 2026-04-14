"""Tests for cowork_hygiene.py — Cowork compatibility memory + legacy-state repair."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "secondbrain" / "scripts"))

from cowork_hygiene import (  # type: ignore[reportMissingImports]
    read_session_start_stamp,
    render_compatibility_memory,
    repair_cowork_hygiene,
)


def _make_runtime(tmp_path: Path) -> dict[str, Path]:
    app_root = tmp_path / "Claude"
    app_root.mkdir()
    desktop_config_path = app_root / "claude_desktop_config.json"
    desktop_config_path.write_text(json.dumps({"mcpServers": {}}))

    workspace_id = "workspace-123"
    runtime_session_id = "session-456"
    runtime_root = app_root / "local-agent-mode-sessions" / workspace_id / runtime_session_id
    plugin_root = runtime_root / "rpm" / "plugin_test" / "secondbrain"
    (plugin_root / ".claude-plugin").mkdir(parents=True)
    (plugin_root / "scripts").mkdir(parents=True, exist_ok=True)
    (plugin_root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "secondbrain", "version": "3.5.21"})
    )

    return {
        "app_root": app_root,
        "desktop_config_path": desktop_config_path,
        "runtime_root": runtime_root,
        "plugin_root": plugin_root,
    }


def _write_memory(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


class TestRenderCompatibilityMemory:
    def test_mentions_secondbrain_and_recursive_search(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()

        content = render_compatibility_memory(vault)

        assert str(vault) in content
        assert "/secondbrain:init" in content
        assert "brain/hot-memory.md" in content
        assert "recursive" in content.lower()
        assert "authoritative manifest" in content.lower()
        assert "memex:" not in content.lower()


class TestRepairCoworkHygiene:
    def test_rewrites_compatibility_memory_and_preserves_native_memory(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        runtime = _make_runtime(tmp_path)

        agent_memory = runtime["runtime_root"] / "agent" / "memory" / "MEMORY.md"
        auto_memory = runtime["runtime_root"] / "outputs" / ".auto-memory" / "MEMORY.md"
        native_space_memory = runtime["runtime_root"] / "spaces" / "space-a" / "memory" / "MEMORY.md"

        _write_memory(
            agent_memory,
            "# Memory Index\n\nMemex skills:\n- /memex:session-start\n- /memex:session-end\n",
        )
        _write_memory(
            auto_memory,
            "# Memory Index\n\nUse /memex:session-start before vault work.\n",
        )
        _write_memory(
            native_space_memory,
            "# Memory\n\n- classical guitar\n",
        )

        result = repair_cowork_hygiene(
            vault_path=vault,
            plugin_root=runtime["plugin_root"],
            desktop_config_path=runtime["desktop_config_path"],
        )

        assert result.changed is True
        assert agent_memory in result.rewritten_memory_files
        assert auto_memory in result.rewritten_memory_files
        assert native_space_memory in result.rewritten_memory_files

        agent_text = agent_memory.read_text()
        assert "/secondbrain:doctor" in agent_text
        assert "/memex:" not in agent_text

        auto_text = auto_memory.read_text()
        assert "brain/hot-memory.md" in auto_text
        assert "/memex:" not in auto_text

        native_text = native_space_memory.read_text()
        assert "/secondbrain:dream-protocol" in native_text
        assert "classical guitar" in native_text

    def test_quarantines_legacy_memex_and_sanitizes_marketplace_registry(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        runtime = _make_runtime(tmp_path)
        app_root = runtime["app_root"]

        manifest = app_root / "cowork_plugins" / ".install-manifests" / "memex@memex.json"
        marketplace = app_root / "cowork_plugins" / "marketplaces" / "memex" / "memex" / "hooks" / "hooks.json"
        known = app_root / "cowork_plugins" / "known_marketplaces.json"
        installed = app_root / "cowork_plugins" / "installed_plugins.json"

        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text('{"id":"memex@memex"}')
        marketplace.parent.mkdir(parents=True, exist_ok=True)
        marketplace.write_text('{"hooks":{"Prompt":[{"hooks":[{"command":"Run /memex:session-start"}]}]}}')
        known.parent.mkdir(parents=True, exist_ok=True)
        known.write_text(
            json.dumps(
                [
                    {"id": "knowledge-work-plugins", "repo": "stjepanvrbic/knowledge-work-plugins"},
                    {"id": "memex", "repo": "Skyfox-io/Memex"},
                ],
                indent=2,
            )
        )
        installed.write_text(
            json.dumps(
                [
                    "productivity@knowledge-work-plugins",
                    "memex@memex",
                ],
                indent=2,
            )
        )

        result = repair_cowork_hygiene(
            vault_path=vault,
            plugin_root=runtime["plugin_root"],
            desktop_config_path=runtime["desktop_config_path"],
        )

        assert result.changed is True
        assert not manifest.exists()
        assert not (app_root / "cowork_plugins" / "marketplaces" / "memex").exists()
        assert "memex" not in known.read_text().lower()
        assert "memex" not in installed.read_text().lower()

        quarantine_root = app_root / "secondbrain-runtime" / "quarantine" / "legacy-memex"
        assert (quarantine_root / "cowork_plugins" / ".install-manifests" / "memex@memex.json").is_file()
        assert (quarantine_root / "cowork_plugins" / "marketplaces" / "memex" / "memex" / "hooks" / "hooks.json").is_file()

        second = repair_cowork_hygiene(
            vault_path=vault,
            plugin_root=runtime["plugin_root"],
            desktop_config_path=runtime["desktop_config_path"],
        )
        assert second.changed is False


class TestSessionStartStamp:
    def test_repair_does_not_destroy_existing_stamp(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        runtime = _make_runtime(tmp_path)

        stamp_dir = runtime["app_root"] / "secondbrain-runtime" / "session-start" / "workspace-123"
        stamp_dir.mkdir(parents=True, exist_ok=True)
        stamp_path = stamp_dir / "session-456.json"
        stamp_path.write_text(
            json.dumps(
                {
                    "timestamp": "2026-04-14T20:19:37Z",
                    "status": "success",
                    "runtime_session_id": "session-456",
                    "workspace_id": "workspace-123",
                },
                indent=2,
            )
        )

