#!/usr/bin/env python3
"""
runtime_resolver.py — shared runtime/config discovery for secondbrain scripts.

This module centralizes the parts of runtime resolution that multiple scripts
need to agree on:

  - vaults.json path (`SECONDBRAIN_VAULTS_CONFIG` aware)
  - Claude Desktop config path (`SECONDBRAIN_CLAUDE_DESKTOP_CONFIG` aware)
  - Obsidian Connect MCP auth/port resolution with precedence:
      explicit args -> env vars -> desktop config

Stdlib only. No side effects at import time.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse


VAULTS_CONFIG_DEFAULT = Path.home() / ".config" / "secondbrain" / "vaults.json"


@dataclass(frozen=True)
class ResolvedObsidianRuntime:
    port: Optional[int]
    api_key: Optional[str]
    port_source: Optional[str]
    api_key_source: Optional[str]
    desktop_config_path: Path
    error: Optional[str] = None


def resolve_vaults_config_path() -> Path:
    override = os.environ.get("SECONDBRAIN_VAULTS_CONFIG")
    return Path(override) if override else VAULTS_CONFIG_DEFAULT


def default_claude_desktop_config_path() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if sys.platform.startswith("win"):
        return Path(os.environ.get("APPDATA", "")) / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def resolve_claude_desktop_config_path(desktop_config_path: Optional[Path] = None) -> Path:
    if desktop_config_path is not None:
        return Path(desktop_config_path)
    override = os.environ.get("SECONDBRAIN_CLAUDE_DESKTOP_CONFIG")
    if override:
        return Path(override)
    return default_claude_desktop_config_path()


def _load_desktop_config(desktop_config_path: Optional[Path] = None) -> tuple[Optional[Dict[str, Any]], Path, Optional[str]]:
    path = resolve_claude_desktop_config_path(desktop_config_path)
    if not path.is_file():
        return None, path, None
    try:
        data = json.loads(path.read_text())
    except Exception as exc:  # noqa: BLE001
        return None, path, str(exc)
    if not isinstance(data, dict):
        return None, path, "desktop config root is not a JSON object"
    return data, path, None


def _obsidian_server(desktop_config_path: Optional[Path] = None) -> tuple[Optional[Dict[str, Any]], Path, Optional[str]]:
    data, path, error = _load_desktop_config(desktop_config_path)
    if data is None:
        return None, path, error
    server = data.get("mcpServers", {}).get("obsidian")
    if not isinstance(server, dict):
        return None, path, error
    return server, path, error


def _extract_port_from_server(server: Dict[str, Any]) -> Optional[int]:
    args = server.get("args", [])
    if not isinstance(args, list):
        return None
    for arg in args:
        if not isinstance(arg, str):
            continue
        parsed = urlparse(arg)
        if parsed.scheme in {"http", "https"} and parsed.port is not None:
            return parsed.port
    return None


def _extract_api_key_from_server(server: Dict[str, Any]) -> Optional[str]:
    env = server.get("env", {})
    if not isinstance(env, dict):
        return None
    raw_auth = env.get("AUTH") or env.get("Authorization")
    if not isinstance(raw_auth, str):
        return None
    raw_auth = raw_auth.strip()
    if not raw_auth:
        return None
    if raw_auth.lower().startswith("bearer "):
        return raw_auth[7:].strip() or None
    return raw_auth


def resolve_obsidian_runtime(
    port: Optional[int] = None,
    api_key: Optional[str] = None,
    desktop_config_path: Optional[Path] = None,
) -> ResolvedObsidianRuntime:
    path = resolve_claude_desktop_config_path(desktop_config_path)
    server, path, config_error = _obsidian_server(desktop_config_path)
    desktop_port = _extract_port_from_server(server) if server is not None else None
    desktop_key = _extract_api_key_from_server(server) if server is not None else None

    if port is not None:
        resolved_port = int(port)
        port_source: Optional[str] = "explicit"
    else:
        env_port = os.environ.get("OBSIDIAN_MCP_PORT")
        if env_port is not None:
            env_port = env_port.strip()
            if not env_port:
                return ResolvedObsidianRuntime(
                    port=None,
                    api_key=None,
                    port_source="env",
                    api_key_source=None,
                    desktop_config_path=path,
                    error="OBSIDIAN_MCP_PORT is set but empty",
                )
            try:
                resolved_port = int(env_port)
            except ValueError:
                return ResolvedObsidianRuntime(
                    port=None,
                    api_key=None,
                    port_source="env",
                    api_key_source=None,
                    desktop_config_path=path,
                    error=f"OBSIDIAN_MCP_PORT is not a valid integer: {env_port!r}",
                )
            port_source = "env"
        else:
            resolved_port = desktop_port
            port_source = "desktop_config" if desktop_port is not None else None

    if api_key is not None:
        resolved_key = api_key.strip()
        key_source: Optional[str] = "explicit"
        if not resolved_key:
            return ResolvedObsidianRuntime(
                port=resolved_port,
                api_key=None,
                port_source=port_source,
                api_key_source="explicit",
                desktop_config_path=path,
                error="explicit api_key is empty",
            )
    else:
        env_key = os.environ.get("OBSIDIAN_API_KEY")
        if env_key is not None:
            resolved_key = env_key.strip()
            key_source = "env"
            if not resolved_key:
                return ResolvedObsidianRuntime(
                    port=resolved_port,
                    api_key=None,
                    port_source=port_source,
                    api_key_source="env",
                    desktop_config_path=path,
                    error="OBSIDIAN_API_KEY is set but empty",
                )
        else:
            resolved_key = desktop_key
            key_source = "desktop_config" if desktop_key is not None else None

    return ResolvedObsidianRuntime(
        port=resolved_port,
        api_key=resolved_key,
        port_source=port_source,
        api_key_source=key_source,
        desktop_config_path=path,
        error=config_error,
    )
