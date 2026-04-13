"""Tests for shared runtime resolution helpers."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "secondbrain" / "scripts"))

from runtime_resolver import (  # type: ignore[reportMissingImports]
    resolve_claude_desktop_config_path,
    resolve_obsidian_runtime,
    resolve_vaults_config_path,
)


class TestResolveVaultsConfigPath:
    def test_honors_env_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        override = tmp_path / "custom-vaults.json"
        monkeypatch.setenv("SECONDBRAIN_VAULTS_CONFIG", str(override))
        assert resolve_vaults_config_path() == override


class TestResolveClaudeDesktopConfigPath:
    def test_honors_env_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        override = tmp_path / "desktop.json"
        monkeypatch.setenv("SECONDBRAIN_CLAUDE_DESKTOP_CONFIG", str(override))
        assert resolve_claude_desktop_config_path() == override

    def test_explicit_path_beats_env_override(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ):
        explicit = tmp_path / "explicit.json"
        env_path = tmp_path / "env.json"
        monkeypatch.setenv("SECONDBRAIN_CLAUDE_DESKTOP_CONFIG", str(env_path))
        assert resolve_claude_desktop_config_path(explicit) == explicit


class TestResolveObsidianRuntime:
    def test_env_only(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OBSIDIAN_MCP_PORT", "27124")
        monkeypatch.setenv("OBSIDIAN_API_KEY", "env-key")
        runtime = resolve_obsidian_runtime()
        assert runtime.port == 27124
        assert runtime.api_key == "env-key"
        assert runtime.port_source == "env"
        assert runtime.api_key_source == "env"
        assert runtime.error is None

    def test_desktop_config_only(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        for var in ("OBSIDIAN_MCP_PORT", "OBSIDIAN_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        cfg = tmp_path / "claude_desktop_config.json"
        cfg.write_text(json.dumps({
            "mcpServers": {
                "obsidian": {
                    "args": [
                        "mcp-remote",
                        "http://localhost:27124/mcp",
                        "--header",
                        "Authorization:${AUTH}",
                    ],
                    "env": {"AUTH": "Bearer desktop-key"},
                }
            }
        }))
        runtime = resolve_obsidian_runtime(desktop_config_path=cfg)
        assert runtime.port == 27124
        assert runtime.api_key == "desktop-key"
        assert runtime.port_source == "desktop_config"
        assert runtime.api_key_source == "desktop_config"
        assert runtime.error is None

    def test_malformed_config_records_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ):
        for var in ("OBSIDIAN_MCP_PORT", "OBSIDIAN_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        cfg = tmp_path / "claude_desktop_config.json"
        cfg.write_text("{not-json")
        runtime = resolve_obsidian_runtime(desktop_config_path=cfg)
        assert runtime.port is None
        assert runtime.api_key is None
        assert runtime.error is not None
