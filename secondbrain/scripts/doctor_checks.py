#!/usr/bin/env python3
"""
doctor_checks.py — read-only check engine for the /secondbrain:doctor skill.

Design
------
T5 turns doctor into a two-turn "diagnose-then-treat" flow:

  Turn 1 (diagnose): run every check in strict read-only mode, print a
  report, end with "I can fix N of these — want me to? (yes/no)".
  Turn 2 (treat): only if the user confirms, invoke the relevant
  `setup_steps` fix functions and re-run the diagnostic.

This module owns the check logic. Each `check_*` function returns a
`CheckResult` (pass/fail/warning + fix hint). `run_all_checks`
orchestrates the full suite and enforces dependency order while still
failing loudly on broken prerequisites, so the report doesn't mislead
with "_MANIFEST.md missing" when the real root cause is "Obsidian isn't
running".

`run_fixable_treatments` is the Phase 2 dispatcher. It iterates the
check results, finds the ones with `fixable=True`, and invokes the
corresponding setup_steps function by name. It must not be called from
Phase 1.

Contract invariants
-------------------
* The module MUST be importable without side effects — no I/O, no
  environment mutation, no `print()` at import time.
* `run_all_checks` does not write files. It may read files to perform
  its checks. This is verified by `tests/test_doctor_two_turn.py` via
  filesystem-hash diff before/after.
* Every check either returns a `CheckResult` or raises; there is no
  third "returned None" state.
* Failures that are auto-fixable must carry `fixable=True` and a
  `fix_function` name that resolves to a `setup_steps` attribute.

Python 3.8+, stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse
from runtime_resolver import (  # type: ignore[reportMissingImports]
    resolve_claude_desktop_config_path,
    resolve_obsidian_runtime,
    resolve_vaults_config_path,
)
from cowork_hygiene import (  # type: ignore[reportMissingImports]
    inspect_cowork_hygiene,
    latest_init_plugins,
    read_session_start_stamp,
)

# Ensure sibling modules (setup_steps, connect_mcp_client) are importable.
# When installed as a plugin, scripts/ is the cwd for hook invocations; when
# running from repo root, we need to add it explicitly so `import setup_steps`
# works from tests too.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


# ---------------------------------------------------------------------------
# CheckResult — the only shape the check functions return
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    """Result of a single doctor check.

    `status` is one of:
      - "pass": everything is fine, nothing to do.
      - "fail": a problem that requires action.
      - "warning": degraded but not broken (e.g. recent ingest failures).
    `fixable` is a hint to Phase 2 — if True, doctor can invoke the
    named `fix_function` in `setup_steps` to resolve it. If False, the
    user must take manual action (documented in the check message).
    """
    name: str
    status: str
    message: str
    fixable: bool
    fix_function: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Individual checks — each one is a pure(ish) function taking only the
# inputs it needs. They never `print()`; logging is the caller's job.
# ---------------------------------------------------------------------------

def _normalize_plugin_root(candidate: Path) -> Optional[Path]:
    """Return the runtime plugin root for a candidate path, if recognizable."""
    if not candidate:
        return None

    candidates = [candidate]
    if candidate.name == "scripts":
        candidates.insert(0, candidate.parent)
    candidates.append(candidate / "secondbrain")

    for path in candidates:
        if path.is_dir() and (path / ".claude-plugin").is_dir() and (path / "scripts").is_dir():
            return path.resolve()

    return candidate.resolve() if candidate.is_dir() else None


def _resolve_plugin_root(candidate: Optional[Path] = None) -> Optional[Path]:
    """Resolve the plugin root from explicit input, env, or the current runtime."""
    for raw in (
        candidate,
        Path(os.environ["CLAUDE_PLUGIN_ROOT"]) if os.environ.get("CLAUDE_PLUGIN_ROOT") else None,
        _SCRIPTS_DIR,
    ):
        if raw is None:
            continue
        resolved = _normalize_plugin_root(Path(raw))
        if resolved is not None:
            return resolved
    return None


def _detect_environment() -> str:
    """Return `code` or `cowork`, defaulting to the safer Cowork mode."""
    try:
        import setup_steps  # type: ignore[reportMissingImports]
        return setup_steps.detect_environment()
    except Exception:
        return "cowork"


def _default_cowork_desktop_config_path() -> Path:
    """Return the Claude Desktop config path for the current platform."""
    return resolve_claude_desktop_config_path()


def _resolve_cowork_app_root(desktop_config_path: Optional[Path] = None) -> Path:
    """Return the Claude Desktop application-data root for the current platform."""
    return resolve_claude_desktop_config_path(desktop_config_path).parent


def _load_cowork_obsidian_server(
    desktop_config_path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Read the Cowork desktop config and return the obsidian MCP server entry."""
    config_path = desktop_config_path or _default_cowork_desktop_config_path()
    if not config_path.is_file():
        return None
    try:
        data = json.loads(config_path.read_text())
    except Exception:
        return None

    server = data.get("mcpServers", {}).get("obsidian")
    return server if isinstance(server, dict) else None


def _cowork_obsidian_api_key(desktop_config_path: Optional[Path] = None) -> Optional[str]:
    """Extract the Cowork obsidian MCP bearer token from desktop config."""
    server = _load_cowork_obsidian_server(desktop_config_path)
    if server is None:
        return None

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


def _cowork_obsidian_port(desktop_config_path: Optional[Path] = None) -> Optional[int]:
    """Extract the Cowork obsidian MCP port from desktop config."""
    server = _load_cowork_obsidian_server(desktop_config_path)
    if server is None:
        return None

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


def _find_plugin_script(plugin_root: Path, script_name: str) -> Optional[Path]:
    """Locate a plugin script across repo and runtime bundle layouts."""
    for candidate in (
        plugin_root / "scripts" / script_name,
        plugin_root / "secondbrain" / "scripts" / script_name,
    ):
        if candidate.is_file():
            return candidate
    return None


def _find_installed_plugin_json(plugin_root: Path) -> Optional[Path]:
    """Locate runtime plugin metadata across repo and runtime layouts."""
    for candidate in (
        plugin_root / ".claude-plugin" / "plugin.json",
        plugin_root / "secondbrain" / ".claude-plugin" / "plugin.json",
    ):
        if candidate.is_file():
            return candidate
    return None


def _extract_cowork_runtime_coords(plugin_root: Path) -> Optional[tuple[str, str]]:
    """Extract `<workspace_id>, <runtime_session_id>` from a Cowork runtime path."""
    parts = plugin_root.resolve().parts
    try:
        idx = parts.index("local-agent-mode-sessions")
    except ValueError:
        return None
    if idx + 2 >= len(parts):
        return None
    return parts[idx + 1], parts[idx + 2]


def _parse_audit_timestamp(raw: Any) -> Optional[datetime]:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _entry_mentions_prompt_too_long(entry: Dict[str, Any]) -> bool:
    try:
        return "Prompt is too long" in json.dumps(entry, sort_keys=True)
    except TypeError:
        return False


_COWORK_BRIDGE_CACHE_READ_THRESHOLD = 500_000
_COWORK_BRIDGE_AGE_THRESHOLD = timedelta(days=7)


def check_cowork_dispatch_bridge(
    environment: Optional[str] = None,
    plugin_root: Optional[Path] = None,
    desktop_config_path: Optional[Path] = None,
) -> CheckResult:
    """Inspect Cowork's active dispatch bridge session for overflow symptoms."""
    env = environment or _detect_environment()
    if env != "cowork":
        return CheckResult(
            name="cowork_dispatch_bridge",
            status="pass",
            message="Cowork dispatch bridge check not applicable outside Cowork",
            fixable=False,
        )

    resolved_plugin_root = _resolve_plugin_root(plugin_root)
    if resolved_plugin_root is None:
        return CheckResult(
            name="cowork_dispatch_bridge",
            status="warning",
            message="cannot resolve plugin root to inspect Cowork dispatch bridge state",
            fixable=False,
        )

    runtime_coords = _extract_cowork_runtime_coords(resolved_plugin_root)
    if runtime_coords is None:
        return CheckResult(
            name="cowork_dispatch_bridge",
            status="warning",
            message=(
                "cannot determine the active Cowork runtime session from the installed plugin path; "
                "doctor cannot inspect bridge overflow from this runtime."
            ),
            fixable=False,
        )

    workspace_id, runtime_session_id = runtime_coords
    app_root = _resolve_cowork_app_root(desktop_config_path)
    bridge_state_path = app_root / "bridge-state.json"
    if not bridge_state_path.is_file():
        return CheckResult(
            name="cowork_dispatch_bridge",
            status="pass",
            message=f"no Cowork bridge-state.json at {bridge_state_path}",
            fixable=False,
        )

    try:
        bridge_state = json.loads(bridge_state_path.read_text())
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            name="cowork_dispatch_bridge",
            status="warning",
            message=f"could not read Cowork bridge-state.json: {exc}",
            fixable=False,
        )

    bridge_key = f"{runtime_session_id}:{workspace_id}"
    bridge_entry = bridge_state.get(bridge_key)
    if not isinstance(bridge_entry, dict):
        return CheckResult(
            name="cowork_dispatch_bridge",
            status="pass",
            message="no active Cowork dispatch bridge mapping for this runtime session",
            fixable=False,
            details={"bridge_state_path": str(bridge_state_path), "bridge_key": bridge_key},
        )

    local_session_id = bridge_entry.get("localSessionId")
    if not isinstance(local_session_id, str) or not local_session_id:
        return CheckResult(
            name="cowork_dispatch_bridge",
            status="warning",
            message=f"Cowork bridge-state entry {bridge_key} is missing localSessionId",
            fixable=False,
        )

    audit_path = (
        app_root
        / "local-agent-mode-sessions"
        / workspace_id
        / runtime_session_id
        / "agent"
        / local_session_id
        / "audit.jsonl"
    )
    if not audit_path.is_file():
        return CheckResult(
            name="cowork_dispatch_bridge",
            status="warning",
            message=f"active Cowork dispatch bridge audit missing: {audit_path}",
            fixable=False,
        )

    first_init_at: Optional[datetime] = None
    max_cache_read_tokens = 0
    prompt_too_long_count = 0

    try:
        with audit_path.open() as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                if (
                    first_init_at is None
                    and entry.get("type") == "system"
                    and entry.get("subtype") == "init"
                ):
                    first_init_at = _parse_audit_timestamp(entry.get("_audit_timestamp"))
                if _entry_mentions_prompt_too_long(entry):
                    prompt_too_long_count += 1
                if entry.get("type") == "result" and entry.get("subtype") == "success":
                    usage = entry.get("usage", {})
                    if isinstance(usage, dict):
                        max_cache_read_tokens = max(
                            max_cache_read_tokens,
                            int(usage.get("cache_read_input_tokens", 0) or 0),
                        )
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            name="cowork_dispatch_bridge",
            status="warning",
            message=f"could not parse Cowork dispatch bridge audit: {exc}",
            fixable=False,
        )

    age_days: Optional[float] = None
    if first_init_at is not None:
        age_days = (datetime.now(timezone.utc) - first_init_at).total_seconds() / 86400.0

    reasons: List[str] = []
    if prompt_too_long_count > 0:
        reasons.append(f"saw {prompt_too_long_count} 'Prompt is too long' result(s)")
    if max_cache_read_tokens >= _COWORK_BRIDGE_CACHE_READ_THRESHOLD:
        reasons.append(
            "max cache_read_input_tokens="
            f"{max_cache_read_tokens} (threshold {_COWORK_BRIDGE_CACHE_READ_THRESHOLD})"
        )
    if age_days is not None and age_days > _COWORK_BRIDGE_AGE_THRESHOLD.total_seconds() / 86400.0:
        reasons.append(f"bridge age {age_days:.1f} days exceeds the 7 days threshold")

    details = {
        "bridge_state_path": str(bridge_state_path),
        "bridge_key": bridge_key,
        "local_session_id": local_session_id,
        "audit_path": str(audit_path),
        "prompt_too_long_count": prompt_too_long_count,
        "max_cache_read_input_tokens": max_cache_read_tokens,
        "bridge_age_days": age_days,
    }

    if reasons:
        remediation = (
            "quit Claude Desktop completely, back up "
            f"{bridge_state_path}, back up or rename {app_root / 'local-agent-mode-sessions'}, "
            "relaunch Claude Desktop, then retry one scheduled dispatch."
        )
        return CheckResult(
            name="cowork_dispatch_bridge",
            status="warning",
            message="Cowork dispatch bridge looks bloated — " + "; ".join(reasons) + ". " + remediation,
            fixable=False,
            details=details,
        )

    health_bits: List[str] = []
    if age_days is not None:
        health_bits.append(f"age {age_days:.1f} days")
    health_bits.append(f"max cache_read_input_tokens {max_cache_read_tokens}")
    return CheckResult(
        name="cowork_dispatch_bridge",
        status="pass",
        message="active Cowork dispatch bridge looks healthy (" + ", ".join(health_bits) + ")",
        fixable=False,
        details=details,
    )




def check_cowork_runtime_plugin(
    environment: Optional[str] = None,
    plugin_root: Optional[Path] = None,
    desktop_config_path: Optional[Path] = None,
) -> CheckResult:
    """Check whether the latest Cowork init event actually loaded secondbrain."""
    env = environment or _detect_environment()
    if env != "cowork":
        return CheckResult(
            name="cowork_runtime_plugin",
            status="pass",
            message="Cowork runtime plugin check not applicable outside Cowork",
            fixable=False,
        )

    plugins = latest_init_plugins(plugin_root=plugin_root, desktop_config_path=desktop_config_path)
    if plugins is None:
        return CheckResult(
            name="cowork_runtime_plugin",
            status="warning",
            message=(
                "cannot inspect the latest Cowork init event for this runtime. "
                "If startup context still looks stale, start a fresh session "
                "after repairing local compatibility memory."
            ),
            fixable=False,
        )

    lowered = {plugin.lower() for plugin in plugins}
    if "secondbrain" not in lowered:
        return CheckResult(
            name="cowork_runtime_plugin",
            status="warning",
            message=(
                "latest Cowork init did not load secondbrain, so this session "
                "never received SessionStart state injection. Repair local state, "
                "then start a fresh session (new chat, clear, compact, or full Claude restart)."
            ),
            fixable=False,
            details={"plugins": plugins},
        )

    return CheckResult(
        name="cowork_runtime_plugin",
        status="pass",
        message="latest Cowork init loaded secondbrain successfully",
        fixable=False,
        details={"plugins": plugins},
    )


def check_cowork_memory_hygiene(
    vault_path: Path,
    environment: Optional[str] = None,
    plugin_root: Optional[Path] = None,
    desktop_config_path: Optional[Path] = None,
) -> CheckResult:
    """Check for stale compatibility MEMORY.md files and legacy memex state."""
    env = environment or _detect_environment()
    if env != "cowork":
        return CheckResult(
            name="cowork_memory_hygiene",
            status="pass",
            message="Cowork memory hygiene check not applicable outside Cowork",
            fixable=False,
        )

    report = inspect_cowork_hygiene(
        vault_path=vault_path,
        plugin_root=plugin_root,
        desktop_config_path=desktop_config_path,
    )
    if not report.applicable:
        return CheckResult(
            name="cowork_memory_hygiene",
            status="warning",
            message="cannot resolve the active Cowork runtime to inspect compatibility memory",
            fixable=False,
        )

    issues: list[str] = []
    if report.stale_memory_files:
        issues.append(f"{len(report.stale_memory_files)} stale compatibility MEMORY.md surface(s)")
    if report.legacy_artifacts:
        issues.append(f"{len(report.legacy_artifacts)} legacy memex artifact(s)")
    if report.registry_files_with_memex:
        issues.append(f"{len(report.registry_files_with_memex)} Cowork registry file(s) still referencing memex")

    if issues:
        return CheckResult(
            name="cowork_memory_hygiene",
            status="fail",
            message="Cowork compatibility memory is stale: " + ", ".join(issues),
            fixable=True,
            fix_function="repair_cowork_hygiene",
            details={
                "stale_memory_files": [str(path) for path in report.stale_memory_files],
                "legacy_artifacts": [str(path) for path in report.legacy_artifacts],
                "registry_files_with_memex": [str(path) for path in report.registry_files_with_memex],
            },
        )

    return CheckResult(
        name="cowork_memory_hygiene",
        status="pass",
        message="Cowork compatibility memory and marketplace state look clean",
        fixable=False,
    )


def check_cowork_session_start_stamp(
    environment: Optional[str] = None,
    plugin_root: Optional[Path] = None,
    desktop_config_path: Optional[Path] = None,
) -> CheckResult:
    """Check the most recent session-start stamp written by emit_hot_memory.py."""
    env = environment or _detect_environment()
    if env != "cowork":
        return CheckResult(
            name="cowork_session_start_stamp",
            status="pass",
            message="Cowork session-start stamp check not applicable outside Cowork",
            fixable=False,
        )

    stamp = read_session_start_stamp(
        plugin_root=plugin_root,
        desktop_config_path=desktop_config_path,
    )
    if stamp is None:
        return CheckResult(
            name="cowork_session_start_stamp",
            status="warning",
            message=(
                "no SessionStart stamp found for this runtime. If state injection was just repaired, "
                "start a fresh session (new chat, clear, compact, or full Claude restart)."
            ),
            fixable=False,
        )

    status = stamp.get("status")
    if status != "success":
        reason = str(stamp.get("fallback_reason") or "unknown fallback")
        return CheckResult(
            name="cowork_session_start_stamp",
            status="warning",
            message=(
                f"latest SessionStart stamp recorded a fallback ({reason}). "
                "Repair local state if needed, then start a fresh session (new chat, clear, compact, or full Claude restart)."
            ),
            fixable=False,
            details=stamp,
        )

    timestamp = stamp.get("timestamp")
    return CheckResult(
        name="cowork_session_start_stamp",
        status="pass",
        message=f"latest SessionStart stamp succeeded at {timestamp}",
        fixable=False,
        details=stamp,
    )

def check_plugin_root(plugin_root: Optional[Path] = None) -> CheckResult:
    """Check 1: resolve the plugin root from env or the current doctor runtime."""
    env_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
    if env_root:
        path = Path(env_root)
        if not path.is_dir():
            return CheckResult(
                name="plugin_root",
                status="fail",
                message=(
                    f"CLAUDE_PLUGIN_ROOT points at {path} which does not exist. "
                    f"Fix: /plugin install stjepanvrbic/secondbrain"
                ),
                fixable=False,
            )

    resolved = _resolve_plugin_root(plugin_root)
    if resolved is None:
        return CheckResult(
            name="plugin_root",
            status="fail",
            message=(
                "CLAUDE_PLUGIN_ROOT is not set. The plugin is not installed or "
                "the shell doesn't know about it. Fix: /plugin install "
                "stjepanvrbic/secondbrain"
            ),
            fixable=False,
        )
    return CheckResult(
        name="plugin_root",
        status="pass",
        message=f"CLAUDE_PLUGIN_ROOT={resolved}",
        fixable=False,
    )


def check_environment(environment: Optional[str] = None) -> CheckResult:
    """Check 2: detect Code vs Cowork (informational only — always passes).

    Uses `setup_steps.detect_environment` so there's one source of truth
    for environment detection across init + doctor.
    """
    try:
        env = environment or _detect_environment()
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            name="environment",
            status="pass",
            message=f"environment detection failed: {exc} (informational)",
            fixable=False,
        )
    pretty = "Claude Code" if env == "code" else "Claude Cowork"
    return CheckResult(
        name="environment",
        status="pass",
        message=f"environment: {pretty}",
        fixable=False,
        details={"env": env},
    )


def check_obsidian_api_key(
    environment: Optional[str] = None,
    desktop_config_path: Optional[Path] = None,
) -> CheckResult:
    """Check 3: Obsidian auth is available from env (Code) or desktop config (Cowork).

    Not auto-fixable: doctor cannot mint an API key — it has to be obtained
    from Obsidian's Connect MCP plugin. Previously this check advertised
    `setup_env_vars` as a fix target, but `setup_env_vars(api_key=None, ...)`
    short-circuits to a no-op, so the fix was a lie. Escalate to init instead.
    """
    env = environment or _detect_environment()
    if env == "cowork":
        resolved = resolve_obsidian_runtime(desktop_config_path=desktop_config_path)
        if resolved.api_key:
            return CheckResult(
                name="obsidian_api_key",
                status="pass",
                message=(
                    "Obsidian API key is available from "
                    + ("Cowork desktop config" if resolved.api_key_source == "desktop_config" else "environment")
                ),
                fixable=False,
            )
        detail = (
            f" Desktop config path: {resolved.desktop_config_path}."
            if resolved.desktop_config_path
            else ""
        )
        if resolved.error:
            detail += f" Parse error: {resolved.error}."
        return CheckResult(
            name="obsidian_api_key",
            status="warning",
            message=(
                "Could not prove the Obsidian API key from the Python subprocess in Cowork; "
                "session-level validation is required."
                + detail
            ),
            fixable=False,
        )

    key = os.environ.get("OBSIDIAN_API_KEY", "")
    if not key:
        return CheckResult(
            name="obsidian_api_key",
            status="fail",
            message=(
                "OBSIDIAN_API_KEY is not set. Doctor cannot mint an API key — "
                "run /secondbrain:init to obtain one from Obsidian's Connect "
                "MCP plugin, or add `export OBSIDIAN_API_KEY=\"<key>\"` to "
                "your shell config (~/.zshrc on Mac, ~/.bashrc on Linux) "
                "manually."
            ),
            fixable=False,
        )
    return CheckResult(
        name="obsidian_api_key",
        status="pass",
        message="OBSIDIAN_API_KEY is set",
        fixable=False,
    )


def check_obsidian_mcp_port(
    environment: Optional[str] = None,
    desktop_config_path: Optional[Path] = None,
) -> CheckResult:
    """Check 4: Obsidian MCP port is available from env (Code) or desktop config (Cowork).

    Not auto-fixable: doctor cannot guess which port the user's Connect MCP
    plugin is bound to. Previously this check advertised `setup_env_vars`,
    but that helper short-circuits when both `api_key` and `port` are None,
    so the fix was a structural no-op. Escalate to init instead.
    """
    env = environment or _detect_environment()
    if env == "cowork":
        resolved = resolve_obsidian_runtime(desktop_config_path=desktop_config_path)
        if resolved.port is not None:
            return CheckResult(
                name="obsidian_mcp_port",
                status="pass",
                message=(
                    f"Obsidian MCP port is available from "
                    f"{'Cowork desktop config' if resolved.port_source == 'desktop_config' else 'environment'}: {resolved.port}"
                ),
                fixable=False,
            )
        detail = (
            f" Desktop config path: {resolved.desktop_config_path}."
            if resolved.desktop_config_path
            else ""
        )
        if resolved.error:
            detail += f" Parse error: {resolved.error}."
        return CheckResult(
            name="obsidian_mcp_port",
            status="warning",
            message=(
                "Could not prove the Obsidian MCP port from the Python subprocess in Cowork; "
                "session-level validation is required."
                + detail
            ),
            fixable=False,
        )

    port_str = os.environ.get("OBSIDIAN_MCP_PORT", "")
    if not port_str:
        return CheckResult(
            name="obsidian_mcp_port",
            status="fail",
            message=(
                "OBSIDIAN_MCP_PORT is not set. The plugin's .mcp.json depends "
                "on it. Run /secondbrain:init to configure it, or add "
                "`export OBSIDIAN_MCP_PORT=\"27124\"` to your shell config "
                "manually."
            ),
            fixable=False,
        )
    try:
        int(port_str)
    except ValueError:
        return CheckResult(
            name="obsidian_mcp_port",
            status="fail",
            message=(
                f"OBSIDIAN_MCP_PORT={port_str!r} is not a valid integer. "
                "Run /secondbrain:init to fix it, or "
                "`export OBSIDIAN_MCP_PORT=\"27124\"` manually."
            ),
            fixable=False,
        )
    return CheckResult(
        name="obsidian_mcp_port",
        status="pass",
        message=f"OBSIDIAN_MCP_PORT={port_str}",
        fixable=False,
    )


def _build_mcp_client(
    environment: Optional[str] = None,
    desktop_config_path: Optional[Path] = None,
) -> Any:
    """Construct a ConnectMCPClient for the current environment."""
    import connect_mcp_client  # type: ignore[reportMissingImports]
    resolved = resolve_obsidian_runtime(desktop_config_path=desktop_config_path)
    if resolved.port is not None and resolved.api_key:
        return connect_mcp_client.ConnectMCPClient(
            port=resolved.port,
            api_key=resolved.api_key,
            desktop_config_path=desktop_config_path,
        )
    return connect_mcp_client.ConnectMCPClient(desktop_config_path=desktop_config_path)


def check_obsidian_running(
    environment: Optional[str] = None,
    desktop_config_path: Optional[Path] = None,
    client_factory: Optional[Callable[[], Any]] = None,
) -> CheckResult:
    """Check 5: Obsidian process is running (best effort, platform-dependent).

    Uses `pgrep` if available, falls back to `ps`. In Cowork, the more
    reliable signal is a live MCP round-trip rather than host process
    inspection from the sandbox.
    """
    env = environment or _detect_environment()
    if env == "cowork":
        try:
            client = client_factory() if client_factory is not None else _build_mcp_client(
                environment=env,
                desktop_config_path=desktop_config_path,
            )
            reachable = bool(client.is_reachable())
        except Exception as exc:  # noqa: BLE001
            return CheckResult(
                name="obsidian_running",
                status="warning",
                message=(
                    "Could not confirm Obsidian from the Cowork Python subprocess: "
                    f"{exc}. Session-level validation is required."
                ),
                fixable=False,
            )
        if reachable:
            return CheckResult(
                name="obsidian_running",
                status="pass",
                message="Obsidian is reachable via Connect MCP in Cowork",
                fixable=False,
            )
        return CheckResult(
            name="obsidian_running",
            status="fail",
            message=(
                "Obsidian is not reachable via Connect MCP in Cowork. "
                "Open Obsidian and make sure the Connect MCP plugin is enabled."
            ),
            fixable=False,
        )

    if shutil.which("pgrep"):
        try:
            r = subprocess.run(
                ["pgrep", "-i", "obsidian"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0 and r.stdout.strip():
                return CheckResult(
                    name="obsidian_running",
                    status="pass",
                    message="Obsidian process detected via pgrep",
                    fixable=False,
                )
            return CheckResult(
                name="obsidian_running",
                status="fail",
                message=(
                    "Obsidian is not running. Open /Applications/Obsidian.app "
                    "(or the Obsidian you installed via package manager)."
                ),
                fixable=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return CheckResult(
                name="obsidian_running",
                status="fail",
                message=f"pgrep failed: {exc}",
                fixable=False,
            )

    if shutil.which("ps"):
        try:
            r = subprocess.run(
                ["ps", "-A", "-o", "comm="],
                capture_output=True,
                text=True,
                timeout=5,
            )
            commands = [line.strip().lower() for line in r.stdout.splitlines()]
            if any("obsidian" in command for command in commands):
                return CheckResult(
                    name="obsidian_running",
                    status="pass",
                    message="Obsidian process detected via ps",
                    fixable=False,
                )
            return CheckResult(
                name="obsidian_running",
                status="fail",
                message=(
                    "Obsidian is not running. Open /Applications/Obsidian.app "
                    "(or the Obsidian you installed via package manager)."
                ),
                fixable=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return CheckResult(
                name="obsidian_running",
                status="fail",
                message=f"ps failed: {exc}",
                fixable=False,
            )

    return CheckResult(
        name="obsidian_running",
        status="fail",
        message="Neither pgrep nor ps is available on this platform — cannot inspect processes",
        fixable=False,
    )


def check_mcp_connection(
    environment: Optional[str] = None,
    desktop_config_path: Optional[Path] = None,
    client_factory: Optional[Callable[[], Any]] = None,
) -> CheckResult:
    """Check 6: Connect MCP is reachable via ConnectMCPClient.

    `client_factory` is injectable for tests — production code passes None
    and we construct a real `ConnectMCPClient()` from env vars. Any
    exception from the factory or reachability check is caught and
    surfaced as a `fail` CheckResult.
    """
    if client_factory is None:
        try:
            import connect_mcp_client  # type: ignore[reportMissingImports]

            env = environment or _detect_environment()

            def _default_factory() -> Any:
                return _build_mcp_client(
                    environment=env,
                    desktop_config_path=desktop_config_path,
                )

            client_factory = _default_factory
        except ImportError as exc:
            return CheckResult(
                name="mcp_connection",
                status="fail",
                message=f"connect_mcp_client not importable: {exc}",
                fixable=False,
            )

    try:
        client = client_factory()
    except Exception as exc:  # noqa: BLE001 — factory may raise anything
        env = environment or _detect_environment()
        if env == "cowork":
            return CheckResult(
                name="mcp_connection",
                status="warning",
                message=(
                    f"Could not construct an MCP client from the Cowork Python subprocess: {exc}. "
                    "Session-level validation is required."
                ),
                fixable=False,
            )
        return CheckResult(
            name="mcp_connection",
            status="fail",
            message=(
                f"Could not construct MCP client: {exc}. Check that "
                "OBSIDIAN_MCP_PORT and OBSIDIAN_API_KEY are set, and that "
                "Obsidian is running with the Connect MCP plugin enabled."
            ),
            fixable=False,
        )

    try:
        reachable = bool(client.is_reachable())
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            name="mcp_connection",
            status="fail",
            message=(
                f"MCP reachability check threw: {exc}. Check that Obsidian "
                "is running and the Connect MCP plugin is enabled."
            ),
            fixable=False,
        )

    if not reachable:
        return CheckResult(
            name="mcp_connection",
            status="fail",
            message=(
                "Connect MCP is not reachable. Obsidian may be closed, the "
                "Connect MCP plugin may be disabled, or OBSIDIAN_MCP_PORT "
                "may be pointing at the wrong port."
            ),
            fixable=False,
        )

    return CheckResult(
        name="mcp_connection",
        status="pass",
        message="Connect MCP is reachable",
        fixable=False,
    )


def check_vault_reachable(vault_path: Path) -> CheckResult:
    """Check 7: the vault directory exists and has at least one .md file."""
    if not vault_path.exists():
        return CheckResult(
            name="vault_reachable",
            status="fail",
            message=f"vault path does not exist: {vault_path}",
            fixable=False,
        )
    if not vault_path.is_dir():
        return CheckResult(
            name="vault_reachable",
            status="fail",
            message=f"vault path is not a directory: {vault_path}",
            fixable=False,
        )

    # Count any .md files (anywhere in the vault).
    has_md = False
    try:
        for _ in vault_path.rglob("*.md"):
            has_md = True
            break
    except OSError as exc:
        return CheckResult(
            name="vault_reachable",
            status="fail",
            message=f"could not walk vault: {exc}",
            fixable=False,
        )

    if not has_md:
        return CheckResult(
            name="vault_reachable",
            status="warning",
            message=(
                f"vault at {vault_path} has no .md files yet. If this is a "
                "fresh vault, run /secondbrain:init to scaffold it."
            ),
            fixable=False,
        )

    return CheckResult(
        name="vault_reachable",
        status="pass",
        message=f"vault reachable at {vault_path}",
        fixable=False,
    )


def check_manifest_exists(vault_path: Path) -> CheckResult:
    """Check 8: `${vault_path}/_MANIFEST.md` exists."""
    manifest = vault_path / "_MANIFEST.md"
    if manifest.exists():
        return CheckResult(
            name="manifest",
            status="pass",
            message="_MANIFEST.md present",
            fixable=False,
        )
    return CheckResult(
        name="manifest",
        status="fail",
        message=(
            "_MANIFEST.md is missing. Doctor can regenerate it during "
            "treatment via rebuild_manifest."
        ),
        fixable=True,
        fix_function="rebuild_manifest",
    )


def check_log_md_exists(vault_path: Path) -> CheckResult:
    """Check 9: `${vault_path}/log.md` exists."""
    log = vault_path / "log.md"
    if log.exists():
        return CheckResult(
            name="log_md",
            status="pass",
            message="log.md present",
            fixable=False,
        )
    return CheckResult(
        name="log_md",
        status="fail",
        message=(
            "log.md (append-only audit trail) is missing. Doctor can "
            "create it during treatment via create_log_md."
        ),
        fixable=True,
        fix_function="create_log_md",
    )


def check_profile_has_user_content(vault_path: Path) -> CheckResult:
    """Check 10: `me/profile.md` contains real content, not `{{PLACEHOLDER}}`."""
    profile = vault_path / "me" / "profile.md"
    if not profile.exists():
        return CheckResult(
            name="profile",
            status="fail",
            message=(
                "me/profile.md is missing. Doctor can seed it from the "
                "template via setup_profile."
            ),
            fixable=True,
            fix_function="setup_profile",
        )
    try:
        content = profile.read_text()
    except OSError as exc:
        return CheckResult(
            name="profile",
            status="fail",
            message=f"could not read {profile}: {exc}",
            fixable=False,
        )
    if "{{" in content:
        return CheckResult(
            name="profile",
            status="fail",
            message=(
                "me/profile.md still has template placeholders (e.g. "
                "{{USER_NAME}}). Run /secondbrain:init, or doctor can "
                "re-seed via setup_profile (you'll still need to fill "
                "in the placeholders)."
            ),
            fixable=True,
            fix_function="setup_profile",
        )
    return CheckResult(
        name="profile",
        status="pass",
        message="me/profile.md has user content",
        fixable=False,
    )


def check_standard_folders(vault_path: Path) -> CheckResult:
    """Check 11: the secondbrain standard folders are present."""
    required = ("brain", "entities", "me", "inbox", "archive")
    missing = [d for d in required if not (vault_path / d).is_dir()]
    if not missing:
        return CheckResult(
            name="standard_folders",
            status="pass",
            message=f"all standard folders present: {', '.join(required)}",
            fixable=False,
        )
    return CheckResult(
        name="standard_folders",
        status="fail",
        message=(
            f"missing standard folders: {', '.join(missing)}. Doctor can "
            "create them via setup_vault_scaffolding."
        ),
        fixable=True,
        fix_function="setup_vault_scaffolding",
        details={"missing": missing},
    )


_SCHEDULED_TASK_ROW_RE = re.compile(r"^\|\s*([^|]+?)\s*\|")


def _load_manifest_task_ids(manifest_path: Path) -> List[str]:
    """Read scheduled-task IDs from MANIFEST.md."""
    task_ids: List[str] = []
    try:
        lines = manifest_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return task_ids

    for line in lines:
        if not line.startswith("|"):
            continue
        if "Task | Cron | Skill | Default" in line or set(line.replace("|", "").strip()) == {"-"}:
            continue
        m = _SCHEDULED_TASK_ROW_RE.match(line)
        if not m:
            continue
        task_id = m.group(1).strip()
        if task_id:
            task_ids.append(task_id)
    return task_ids


def _normalize_actual_scheduled_tasks(actual_tasks: List[Any]) -> tuple[Dict[str, bool], List[str]]:
    """Normalize runtime scheduled-task inventory into task_id -> enabled."""
    normalized: Dict[str, bool] = {}
    extras: List[str] = []

    for task in actual_tasks:
        if isinstance(task, str):
            normalized[task] = True
            continue
        if not isinstance(task, dict):
            extras.append(repr(task))
            continue
        task_id = task.get("taskId")
        if not isinstance(task_id, str) or not task_id:
            extras.append(repr(task))
            continue
        enabled = task.get("enabled", True)
        normalized[task_id] = bool(enabled)

    return normalized, extras


def check_scheduled_tasks(
    env: str,
    actual_tasks: Optional[List[Any]] = None,
    manifest_path: Optional[Path] = None,
) -> CheckResult:
    """Check 12: bundled scheduled tasks are registered.

    This is intentionally best-effort. In Code, we'd want to call
    `CronList` — but that's only available from inside an agent session,
    not from a Python subprocess. So doctor's read-only check can't
    definitively verify the state; it returns a warning with a note telling
    the agent to verify via CronList in the surrounding skill body.

    In Cowork, the authoritative verification lives in the surrounding
    session layer via the scheduled-tasks tool, not in a filesystem probe.
    """
    if actual_tasks is None:
        if env == "code":
            return CheckResult(
                name="scheduled_tasks",
                status="warning",
                message=(
                    "scheduled-task registration must be verified via CronList "
                    "from the agent session — doctor's Python subprocess cannot "
                    "call it directly. The skill body does this check separately."
                ),
                fixable=False,
            )

        # Cowork: try to locate the workspace root. In the absence of a clear
        # contract, degrade to a warning.
        return CheckResult(
            name="scheduled_tasks",
            status="warning",
            message=(
                "scheduled-task presence in Cowork must be verified from the session layer "
                "via the scheduled-tasks tool — doctor cannot definitively check this from a subprocess."
            ),
            fixable=False,
        )

    resolved_manifest = manifest_path
    if resolved_manifest is None:
        plugin_root = _resolve_plugin_root()
        if plugin_root is not None:
            candidate = plugin_root / "scheduled-tasks" / "MANIFEST.md"
            if candidate.is_file():
                resolved_manifest = candidate

    if resolved_manifest is None or not resolved_manifest.is_file():
        return CheckResult(
            name="scheduled_tasks",
            status="fail",
            message="scheduled-tasks MANIFEST.md missing — cannot validate runtime inventory",
            fixable=False,
        )

    manifest_task_ids = _load_manifest_task_ids(resolved_manifest)
    if not manifest_task_ids:
        return CheckResult(
            name="scheduled_tasks",
            status="fail",
            message=f"{resolved_manifest} has no task rows — scheduled-task contract is undefined",
            fixable=False,
        )

    actual_by_id, malformed = _normalize_actual_scheduled_tasks(actual_tasks)
    missing = [task_id for task_id in manifest_task_ids if task_id not in actual_by_id]
    disabled = [task_id for task_id in manifest_task_ids if task_id in actual_by_id and not actual_by_id[task_id]]
    extras = sorted(task_id for task_id in actual_by_id if task_id not in manifest_task_ids)
    issues: List[str] = []
    if missing:
        issues.append("missing: " + ", ".join(missing))
    if disabled:
        issues.append("disabled: " + ", ".join(disabled))
    if malformed:
        issues.append("malformed entries: " + ", ".join(malformed))
    if issues:
        return CheckResult(
            name="scheduled_tasks",
            status="fail",
            message="scheduled-task contract mismatch — " + "; ".join(issues),
            fixable=False,
            details={
                "manifest_tasks": manifest_task_ids,
                "missing": missing,
                "disabled": disabled,
                "malformed": malformed,
            },
        )
    if extras:
        return CheckResult(
            name="scheduled_tasks",
            status="warning",
            message=(
                "manifest tasks present and enabled, but runtime has extra scheduled tasks: "
                + ", ".join(extras)
            ),
            fixable=False,
            details={"manifest_tasks": manifest_task_ids, "extras": extras},
        )

    return CheckResult(
        name="scheduled_tasks",
        status="pass",
        message=(
            f"{len(manifest_task_ids)} manifest scheduled tasks present and enabled: "
            + ", ".join(manifest_task_ids)
        ),
        fixable=False,
        details={"manifest_tasks": manifest_task_ids},
    )


_LOG_DREAM_RE = re.compile(
    r"^##\s+\[(\d{4}-\d{2}-\d{2})\s+\d{2}:\d{2}\]\s+dream-protocol\s+\|\s+(.*)$",
    re.MULTILINE,
)


def check_last_dream_protocol_run(vault_path: Path) -> CheckResult:
    """Check 13: examine the most recent dream-protocol entry in log.md.

    Informational — reports pass when the most recent run was clean,
    warning when it mentions "issues" or "errors", warning if the log is
    missing or there has never been a run. Never fixable.
    """
    log = vault_path / "log.md"
    if not log.exists():
        return CheckResult(
            name="last_dream_protocol_run",
            status="warning",
            message="log.md missing — cannot check last dream-protocol run",
            fixable=False,
        )
    try:
        text = log.read_text()
    except OSError as exc:
        return CheckResult(
            name="last_dream_protocol_run",
            status="warning",
            message=f"could not read log.md: {exc}",
            fixable=False,
        )

    matches = list(_LOG_DREAM_RE.finditer(text))
    if not matches:
        return CheckResult(
            name="last_dream_protocol_run",
            status="warning",
            message="no dream-protocol entries in log.md yet",
            fixable=False,
        )

    last = matches[-1]
    date_str = last.group(1)
    summary = last.group(2).strip()
    if re.search(r"\b(error|errors|issue|issues|fail|failed)\b", summary, re.IGNORECASE):
        return CheckResult(
            name="last_dream_protocol_run",
            status="warning",
            message=(
                f"most recent dream-protocol ({date_str}) reported issues: "
                f"{summary}"
            ),
            fixable=False,
        )
    return CheckResult(
        name="last_dream_protocol_run",
        status="pass",
        message=f"most recent dream-protocol ({date_str}) ran cleanly",
        fixable=False,
    )


# ---------------------------------------------------------------------------
# New T5 checks
# ---------------------------------------------------------------------------

def check_vault_identity_cross(
    vault_path: Path,
    mcp_client: Optional[Any] = None,
) -> CheckResult:
    """Check 6.5 (new): cross-verify vault_id from filesystem vs MCP read.

    Reads `.secondbrain-installed` via local filesystem, then reads it
    again via `mcp_client.vault_read("/.secondbrain-installed")`, and
    compares the `vault_id` fields. A mismatch means VAULT_PATH and the
    vault Obsidian has open are different vaults — a config conflict
    that doctor CANNOT auto-fix (it requires manual reconciliation).
    """
    marker = vault_path / ".secondbrain-installed"

    # FS side first.
    if not marker.exists():
        # Doctor CANNOT auto-fix this — setup_steps.write_vault_id explicitly
        # refuses to create a missing marker ("init must create it first").
        # Advertising fixable=True here would lie to the user: they'd approve
        # the fix, nothing would happen, and the re-diagnostic would show the
        # same failure. Escalate to /secondbrain:init instead.
        return CheckResult(
            name="vault_identity_cross",
            status="fail",
            message=(
                f"vault marker missing at {marker}. Doctor cannot create the "
                "marker from scratch — only /secondbrain:init can. Run "
                "/secondbrain:init against this vault to bootstrap it."
            ),
            fixable=False,
        )

    try:
        fs_data = json.loads(marker.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return CheckResult(
            name="vault_identity_cross",
            status="fail",
            message=f"could not parse local marker: {exc}",
            fixable=False,
        )
    fs_vault_id = fs_data.get("vault_id") if isinstance(fs_data, dict) else None
    if not fs_vault_id:
        return CheckResult(
            name="vault_identity_cross",
            status="fail",
            message=(
                f"local marker at {marker} has no vault_id. Doctor can stamp "
                "one via write_vault_id."
            ),
            fixable=True,
            fix_function="write_vault_id",
        )

    # MCP side — if no client was provided, we can't cross-check.
    if mcp_client is None:
        return CheckResult(
            name="vault_identity_cross",
            status="warning",
            message="no MCP client available to cross-check vault_id — session-level validation is required",
            fixable=False,
        )

    try:
        mcp_raw = mcp_client.vault_read(".secondbrain-installed")
    except Exception as exc:  # noqa: BLE001
        exc_message = str(exc).lower()
        if isinstance(exc, FileNotFoundError) or "file not found" in exc_message:
            return CheckResult(
                name="vault_identity_cross",
                status="warning",
                message=(
                    "MCP could not read .secondbrain-installed. Dotfiles may be hidden "
                    "from Obsidian MCP in this environment, so doctor cannot prove the "
                    "open vault matches VAULT_PATH."
                ),
                fixable=False,
            )
        return CheckResult(
            name="vault_identity_cross",
            status="fail",
            message=(
                f"MCP vault_read failed for .secondbrain-installed: {exc}. "
                "Obsidian may have no such file in the open vault — are "
                "VAULT_PATH and the open Obsidian vault the same vault?"
            ),
            fixable=False,
        )

    try:
        mcp_data = json.loads(mcp_raw) if mcp_raw else {}
    except json.JSONDecodeError as exc:
        return CheckResult(
            name="vault_identity_cross",
            status="fail",
            message=f"MCP read returned unparseable JSON: {exc}",
            fixable=False,
        )
    mcp_vault_id = mcp_data.get("vault_id") if isinstance(mcp_data, dict) else None

    if fs_vault_id != mcp_vault_id:
        return CheckResult(
            name="vault_identity_cross",
            status="fail",
            message=(
                "VAULT IDENTITY MISMATCH: the vault Obsidian has open is "
                f"NOT the same vault VAULT_PATH points at. FS vault_id="
                f"{fs_vault_id}, MCP vault_id={mcp_vault_id}. "
                "This requires manual reconciliation — doctor CANNOT auto-fix "
                "a wrong-vault config conflict. Either change VAULT_PATH to "
                "match the open vault, or switch Obsidian to the intended "
                "vault and re-run doctor."
            ),
            fixable=False,
            details={"fs_vault_id": fs_vault_id, "mcp_vault_id": mcp_vault_id},
        )

    return CheckResult(
        name="vault_identity_cross",
        status="pass",
        message=f"vault_id matches across FS and MCP: {fs_vault_id}",
        fixable=False,
    )


def check_hot_memory_schema(vault_path: Path, plugin_root: Path) -> CheckResult:
    """Check 14: `brain/hot-memory.md` validates against the T10 schema.

    As of T11 the hot-memory file lives at `<vault>/brain/hot-memory.md`
    (NOT the old `.secondbrain/hot-memory.json` placeholder) and is validated
    via `validate_hot_memory.py --quiet <path>`.

    Behavior:
      - `validate_hot_memory.py` missing at the expected script path → fail
        with explanation (usually means a partial install).
      - hot-memory file missing → fail with a pointer to dream-protocol.
      - hot-memory file present but invalid → fail with the validator's
        stderr captured in the message.
      - All-good → pass.
    """
    validator = _find_plugin_script(plugin_root, "validate_hot_memory.py")
    if validator is None:
        return CheckResult(
            name="hot_memory_schema",
            status="fail",
            message=(
                "validate_hot_memory.py is not present at "
                f"{plugin_root}. The plugin install is incomplete, so the "
                "hot-memory check cannot run."
            ),
            fixable=False,
        )

    hot_memory = vault_path / "brain" / "hot-memory.md"
    if not hot_memory.exists():
        return CheckResult(
            name="hot_memory_schema",
            status="fail",
            message=(
                f"hot-memory file missing at {hot_memory}. Doctor can "
                "seed it from the T10 INITIAL_TEMPLATE — run "
                "/secondbrain:doctor and answer 'yes' to the treatment "
                "prompt, or run /secondbrain:dream-protocol to regenerate "
                "from live vault state."
            ),
            fixable=True,
            fix_function="create_hot_memory_initial",
        )

    # Delegate to the real validator so doctor and dream-protocol share one
    # source of truth on what "valid" means.
    try:
        r = subprocess.run(
            [
                sys.executable,
                str(validator),
                "--quiet",
                str(hot_memory),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CheckResult(
            name="hot_memory_schema",
            status="fail",
            message=f"validate_hot_memory.py failed to run: {exc}",
            fixable=False,
        )

    if r.returncode != 0:
        err_snippet = (r.stderr or r.stdout or "").strip().splitlines()
        first_line = err_snippet[0] if err_snippet else "unknown error"
        return CheckResult(
            name="hot_memory_schema",
            status="fail",
            message=(
                f"hot-memory failed validation: {first_line}. "
                "Run /secondbrain:dream-protocol to rebuild, or "
                "/secondbrain:doctor --fix once that's wired up."
            ),
            fixable=False,
        )

    return CheckResult(
        name="hot_memory_schema",
        status="pass",
        message=f"hot-memory.md validates cleanly at {hot_memory}",
        fixable=False,
    )


def check_core_hooks_path(repo_root: Path) -> CheckResult:
    """Check 15 (new): the secondbrain repo has `core.hooksPath=.githooks`.

    Catches the "fresh clone forgot install_git_hooks.py" case. This is
    informational — doctor can't fix it because running the installer
    requires the repo to actually BE the cwd, which it may not be.
    """
    git_dir = repo_root / ".git"
    if not git_dir.exists():
        return CheckResult(
            name="core_hooks_path",
            status="fail",
            message=f"{repo_root} is not a git repo — cannot check core.hooksPath",
            fixable=False,
        )

    try:
        r = subprocess.run(
            ["git", "-C", str(repo_root), "config", "--get", "core.hooksPath"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CheckResult(
            name="core_hooks_path",
            status="fail",
            message=f"git config failed: {exc}",
            fixable=False,
        )

    value = r.stdout.strip()
    if r.returncode != 0 or not value:
        return CheckResult(
            name="core_hooks_path",
            status="fail",
            message=(
                f"core.hooksPath is not set in {repo_root}. This means the "
                "pre-push safety hook is not wired up. Fix: run "
                "`python3 secondbrain/scripts/install_git_hooks.py` from the "
                "repo root."
            ),
            fixable=False,
        )

    if value != ".githooks":
        return CheckResult(
            name="core_hooks_path",
            status="warning",
            message=(
                f"core.hooksPath in {repo_root} is {value!r}, expected "
                "'.githooks'. Either this is a custom setup or you need to "
                "re-run install_git_hooks.py."
            ),
            fixable=False,
        )

    return CheckResult(
        name="core_hooks_path",
        status="pass",
        message=f"core.hooksPath={value} in {repo_root}",
        fixable=False,
    )


_INGEST_LOG_RE = re.compile(
    r"^##\s+\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\]\s+(\w+)"
)


def check_ingest_log_recent_failures(
    vault_path: Path,
    hours: int = 24,
) -> CheckResult:
    """Check 16 (new): scan `${vault}/.secondbrain/ingest-log.md` for failures.

    The file is written by the Phase 2 on-stop hook and Phase 3 ingester.
    If it doesn't exist yet, return `pass` (no failures to report). If
    it exists, parse recent entries (within `hours`) and look for any
    whose status contains "fail" or "error".
    """
    log = vault_path / ".secondbrain" / "ingest-log.md"
    if not log.exists():
        return CheckResult(
            name="ingest_log_recent_failures",
            status="pass",
            message="no ingest-log.md yet (nothing to report)",
            fixable=False,
        )
    try:
        text = log.read_text()
    except OSError as exc:
        return CheckResult(
            name="ingest_log_recent_failures",
            status="warning",
            message=f"could not read ingest log: {exc}",
            fixable=False,
        )

    cutoff = datetime.now() - timedelta(hours=hours)
    recent_failures: List[str] = []
    for line in text.splitlines():
        m = _INGEST_LOG_RE.match(line.rstrip())
        if not m:
            continue
        try:
            ts = datetime.fromisoformat(m.group(1))
        except ValueError:
            continue
        if ts < cutoff:
            continue
        status = m.group(2).lower()
        if "fail" in status or "error" in status:
            recent_failures.append(line.rstrip())

    if recent_failures:
        return CheckResult(
            name="ingest_log_recent_failures",
            status="warning",
            message=(
                f"found {len(recent_failures)} recent failure(s) in ingest-log.md "
                f"(last {hours}h): {recent_failures[0]}"
            ),
            fixable=False,
            details={"recent_failures": recent_failures},
        )
    return CheckResult(
        name="ingest_log_recent_failures",
        status="pass",
        message="no recent failures in ingest-log.md",
        fixable=False,
    )


def check_legacy_claude_md(vault_path: Path) -> CheckResult:
    """Check if deprecated CLAUDE.md exists at vault root."""
    claude_md = vault_path / "CLAUDE.md"
    if not claude_md.is_file():
        return CheckResult(
            name="legacy_claude_md",
            status="pass",
            message="no legacy CLAUDE.md at vault root",
            fixable=False,
        )
    return CheckResult(
        name="legacy_claude_md",
        status="warning",
        message=(
            "Legacy CLAUDE.md at vault root — deprecated since v3.3.3. "
            "Routing rules are now injected by the plugin. "
            "This file may pollute agent context. Safe to delete or archive."
        ),
        fixable=False,
    )


def check_vaults_config() -> CheckResult:
    """Check if ~/.config/secondbrain/vaults.json exists with an active vault.

    Without this file, all hooks (emit-hot-memory, on-stop, session-end,
    enforce-mcp-only) fail silently — no session logging, no per-turn
    commits, no immutability enforcement.
    """
    config_path = resolve_vaults_config_path()
    if not config_path.exists():
        return CheckResult(
            name="vaults_config",
            status="fail",
            message=(
                f"{config_path} missing — all hooks disabled "
                "(no session logging, no per-turn commits, no immutability enforcement). "
                "Run /secondbrain:init to create it."
            ),
            fixable=True,
            fix_function="add_vault_to_config",
        )
    try:
        data = json.loads(config_path.read_text())
        active_id = data.get("active_vault_id")
        if not active_id:
            return CheckResult(
                name="vaults_config",
                status="fail",
                message="vaults.json exists but has no active_vault_id",
                fixable=True,
                fix_function="add_vault_to_config",
            )
    except Exception as exc:
        return CheckResult(
            name="vaults_config",
            status="fail",
            message=f"vaults.json exists but is malformed: {exc}",
            fixable=True,
            fix_function="add_vault_to_config",
        )
    return CheckResult(
        name="vaults_config",
        status="pass",
        message="vaults.json has active vault configured",
        fixable=False,
    )


def _parse_repo_slug(repository_url: str) -> Optional[str]:
    parsed = urlparse(repository_url)
    path = parsed.path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    if path.count("/") != 1:
        return None
    return path


def _fetch_latest_release_tag(repo_slug: str) -> str:
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo_slug}/releases/latest",
        headers={"Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    tag_name = data.get("tag_name")
    if not isinstance(tag_name, str) or not tag_name:
        raise ValueError("latest release payload missing tag_name")
    return tag_name


def check_plugin_version_mismatch(
    plugin_root: Path,
    latest_release_fetcher: Optional[Callable[[str], str]] = None,
) -> CheckResult:
    """Compare installed runtime plugin version against the latest published release.

    Cowork's extracted runtime bundle carries its own `plugin.json`. That file
    is the concrete thing the user is actually running, so doctor compares its
    version against the latest GitHub release tag and warns cleanly when the
    latest-release lookup is unavailable.
    """
    plugin_json = _find_installed_plugin_json(plugin_root)
    if plugin_json is None:
        return CheckResult(
            name="plugin_version_mismatch",
            status="fail",
            message="cannot find installed plugin.json",
            fixable=False,
        )

    try:
        plugin_data = json.loads(plugin_json.read_text())
    except Exception:
        return CheckResult(
            name="plugin_version_mismatch",
            status="fail",
            message="cannot read installed plugin metadata",
            fixable=False,
        )

    installed_version = plugin_data.get("version")
    if not isinstance(installed_version, str) or not installed_version:
        return CheckResult(
            name="plugin_version_mismatch",
            status="fail",
            message="installed plugin.json is missing a version field",
            fixable=False,
        )

    repo_slug = _parse_repo_slug(str(plugin_data.get("repository") or ""))
    if not repo_slug:
        return CheckResult(
            name="plugin_version_mismatch",
            status="fail",
            message="cannot determine repository slug for latest-release check",
            fixable=False,
        )

    fetcher = latest_release_fetcher or _fetch_latest_release_tag
    try:
        latest_tag = fetcher(repo_slug)
    except Exception as exc:
        return CheckResult(
            name="plugin_version_mismatch",
            status="warning",
            message=f"latest release check unavailable: {exc}",
            fixable=False,
        )

    latest_version = latest_tag.lstrip("v")
    if installed_version == latest_version:
        return CheckResult(
            name="plugin_version_mismatch",
            status="pass",
            message=f"installed runtime plugin version {installed_version} matches latest release",
            fixable=False,
        )

    return CheckResult(
        name="plugin_version_mismatch",
        status="warning",
        message=(
            f"installed runtime plugin version {installed_version} but latest release is "
            f"{latest_version}. In Cowork, remove and reinstall the plugin from the marketplace."
        ),
        fixable=False,
    )


def check_vault_verification(vault_path: Path) -> CheckResult:
    """Run verify_vault.py's default checks (read-only) and report counts.

    This surfaces vault content issues (broken wikilinks, stale inbox, orphans)
    that only Dream Protocol can fix. Doctor reports the counts; Phase 2
    escalates to /secondbrain:dream-protocol if the user confirms.
    """
    try:
        import verify_vault  # type: ignore[reportMissingImports]
    except ImportError:
        return CheckResult(
            name="vault_verification",
            status="fail",
            message="verify_vault.py not importable",
            fixable=False,
        )

    try:
        index = verify_vault.VaultIndex(vault_path)
        checker_results = []
        for name in verify_vault.DEFAULT_CHECKS:
            checker_cls = verify_vault.ALL_CHECKERS.get(name)
            if checker_cls:
                checker_results.append(checker_cls().run(index))
    except Exception as exc:
        return CheckResult(
            name="vault_verification",
            status="warning",
            message=f"verify_vault failed to run: {exc}",
            fixable=False,
        )

    total_errors = sum(
        sum(1 for i in r.issues if i.severity == "error")
        for r in checker_results
    )
    total_warnings = sum(
        sum(1 for i in r.issues if i.severity == "warning")
        for r in checker_results
    )

    if total_errors == 0 and total_warnings == 0:
        return CheckResult(
            name="vault_verification",
            status="pass",
            message="vault verification: no errors or warnings",
            fixable=False,
        )

    status = "fail" if total_errors > 0 else "warning"
    return CheckResult(
        name="vault_verification",
        status=status,
        message=(
            f"vault verification: {total_errors} error(s), {total_warnings} warning(s) — "
            f"run /secondbrain:dream-protocol to fix"
        ),
        fixable=False,
        details={"errors": total_errors, "warnings": total_warnings},
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_all_checks(
    vault_path: Path,
    repo_root: Optional[Path] = None,
    plugin_root: Optional[Path] = None,
    environment: Optional[str] = None,
    desktop_config_path: Optional[Path] = None,
    mcp_client_factory: Optional[Callable[[], Any]] = None,
) -> List[CheckResult]:
    """Run every check in dependency order and return the results.

    Order matters: vault and MCP prerequisites should fail loudly rather
    than disappearing behind skips.

    `repo_root` is for `check_core_hooks_path` — pass the secondbrain
    repo clone. If None, the check degrades to a warning.

    `plugin_root` is for `check_hot_memory_schema` — pass the
    `CLAUDE_PLUGIN_ROOT` path. If None, defaults to the env var.

    `mcp_client_factory` is for test injection into `check_mcp_connection`.
    Production callers pass None.
    """
    plugin_root = _resolve_plugin_root(plugin_root) or Path(
        plugin_root or os.environ.get("CLAUDE_PLUGIN_ROOT", "")
    )
    env = environment or _detect_environment()
    results: List[CheckResult] = []

    # Check 0 — vaults.json config (hooks depend on this, run first).
    results.append(check_vaults_config())

    # Checks 1-5 don't need vault access.
    results.append(check_plugin_root(plugin_root))
    results.append(check_environment(environment=env))
    results.append(
        check_cowork_dispatch_bridge(
            environment=env,
            plugin_root=plugin_root,
            desktop_config_path=desktop_config_path,
        )
    )
    results.append(
        check_cowork_runtime_plugin(
            environment=env,
            plugin_root=plugin_root,
            desktop_config_path=desktop_config_path,
        )
    )
    results.append(
        check_cowork_session_start_stamp(
            environment=env,
            plugin_root=plugin_root,
            desktop_config_path=desktop_config_path,
        )
    )
    api_key_result = check_obsidian_api_key(
        environment=env,
        desktop_config_path=desktop_config_path,
    )
    results.append(api_key_result)
    port_result = check_obsidian_mcp_port(
        environment=env,
        desktop_config_path=desktop_config_path,
    )
    results.append(port_result)
    results.append(
        check_obsidian_running(
            environment=env,
            desktop_config_path=desktop_config_path,
            client_factory=mcp_client_factory if env == "cowork" else None,
        )
    )

    # Check 6 — MCP connection.
    if api_key_result.status == "fail" or port_result.status == "fail":
        mcp_result = CheckResult(
            name="mcp_connection",
            status="fail",
            message="cannot check MCP because obsidian_api_key and/or obsidian_mcp_port failed",
            fixable=False,
        )
    elif env == "cowork" and (
        api_key_result.status == "warning" or port_result.status == "warning"
    ):
        mcp_result = CheckResult(
            name="mcp_connection",
            status="warning",
            message="cannot prove MCP connectivity from the Cowork Python subprocess — session-level validation is required",
            fixable=False,
        )
    else:
        mcp_result = check_mcp_connection(
            environment=env,
            desktop_config_path=desktop_config_path,
            client_factory=mcp_client_factory,
        )
    results.append(mcp_result)

    # MCP client — reused by check_vault_identity_cross.
    mcp_client: Optional[Any] = None
    if mcp_result.status == "pass":
        # Build a real client for downstream checks unless tests injected a factory.
        if mcp_client_factory is not None:
            try:
                mcp_client = mcp_client_factory()
            except Exception:  # noqa: BLE001
                mcp_client = None
        else:
            try:
                mcp_client = _build_mcp_client(
                    environment=env,
                    desktop_config_path=desktop_config_path,
                )
            except Exception:  # noqa: BLE001
                mcp_client = None

    # Check 6.5 — vault identity cross-check. We call it even when MCP is
    # down so the filesystem-side marker validation still runs.
    results.append(check_vault_identity_cross(vault_path, mcp_client=mcp_client))

    # Checks 7-13 — vault-side filesystem checks. `vault_reachable` runs
    # unconditionally because a wrong VAULT_PATH should surface directly.
    vault_reachable_result = check_vault_reachable(vault_path)
    results.append(vault_reachable_result)

    if vault_reachable_result.status == "fail":
        # Cascading fail for filesystem-dependent checks. Any check whose
        # normal-path branch runs only when the vault is reachable must be
        # emitted here as a fail — otherwise callers that index results by
        # name will see a ragged shape between healthy and broken vaults.
        for name in (
            "manifest", "log_md", "profile", "standard_folders",
            "scheduled_tasks", "last_dream_protocol_run",
            "hot_memory_schema", "cowork_memory_hygiene", "ingest_log_recent_failures",
            "vault_verification", "legacy_claude_md", "plugin_version_mismatch",
        ):
            results.append(CheckResult(
                name=name,
                status="fail",
                message="cannot check because vault path is unreachable",
                fixable=False,
            ))
    else:
        results.append(check_manifest_exists(vault_path))
        results.append(check_log_md_exists(vault_path))
        results.append(check_profile_has_user_content(vault_path))
        results.append(check_standard_folders(vault_path))

        # Env-dependent — best-effort, but we can at least tell CronList
        # to run from outside this module.
        results.append(check_scheduled_tasks(env))

        results.append(check_last_dream_protocol_run(vault_path))

        # Phase 3 deferred check
        results.append(check_hot_memory_schema(vault_path, plugin_root))

        results.append(
            check_cowork_memory_hygiene(
                vault_path=vault_path,
                environment=env,
                plugin_root=plugin_root,
                desktop_config_path=desktop_config_path,
            )
        )

        # Phase 2/3 deferred check
        results.append(check_ingest_log_recent_failures(vault_path))

        # Vault content verification (read-only). Escalates to dream-protocol.
        results.append(check_vault_verification(vault_path))

        # Legacy artifact and version checks.
        results.append(check_legacy_claude_md(vault_path))
        results.append(check_plugin_version_mismatch(plugin_root))

    # Check 15 — core.hooksPath (informational, needs the secondbrain repo root).
    if repo_root is not None:
        results.append(check_core_hooks_path(repo_root))
    else:
        results.append(CheckResult(
            name="core_hooks_path",
            status="warning",
            message="repo_root not provided — cannot check core.hooksPath",
            fixable=False,
        ))

    return results


# ---------------------------------------------------------------------------
# Treatment dispatcher — Phase 2 only. Never called from --diagnose.
# ---------------------------------------------------------------------------

def run_fixable_treatments(
    results: List[CheckResult],
    vault_path: Path,
    interactive: bool = True,
) -> List[Any]:
    """Iterate over failed-and-fixable checks and invoke their fix_function.

    Returns a list of `setup_steps.StepResult` — one per attempted fix.
    The dispatcher looks up each `fix_function` as an attribute of the
    `setup_steps` module and calls it with the vault path.

    `interactive=True` is forwarded to helpers that support it (currently
    only `setup_profile`, which walks the user through profile prompts).
    """
    import setup_steps  # type: ignore[reportMissingImports]

    fix_priority = {
        "write_vault_id": 0,
        "add_vault_to_config": 1,
        "create_hot_memory_initial": 2,
        "repair_cowork_hygiene": 3,
    }
    pending_results = sorted(
        (
            result
            for result in results
            if result.status == "fail" and result.fixable and result.fix_function
        ),
        key=lambda result: (fix_priority.get(result.fix_function or "", 100), result.name),
    )

    step_results: List[Any] = []
    for result in pending_results:
        fix_fn = getattr(setup_steps, result.fix_function, None)
        if fix_fn is None:
            # Missing implementation — surface the mismatch but don't crash.
            step_results.append(setup_steps.StepResult(
                success=False,
                message=(
                    f"treatment dispatch: no setup_steps.{result.fix_function} "
                    f"for check {result.name}"
                ),
                did_work=False,
                error="fix_function does not exist",
            ))
            continue

        # Each fix function takes (vault_path: Path) plus optional kwargs.
        # Checks that used to dispatch setup_env_vars are now fixable=False —
        # doctor cannot mint an API key or guess a port, so those failures
        # escalate to /secondbrain:init and never reach this dispatcher.
        try:
            if result.fix_function == "setup_profile":
                step_result = fix_fn(vault_path, interactive=interactive)
            elif result.fix_function == "add_vault_to_config":
                # Needs a vault_id and name — read them from the marker.
                marker = vault_path / ".secondbrain-installed"
                try:
                    marker_data = json.loads(marker.read_text()) if marker.exists() else {}
                except (OSError, json.JSONDecodeError):
                    marker_data = {}
                vid = marker_data.get("vault_id") if isinstance(marker_data, dict) else None
                if not isinstance(vid, str) or not vid:
                    step_result = setup_steps.StepResult(
                        success=False,
                        message=(
                            f"add_vault_to_config: marker has no vault_id — "
                            "run write_vault_id first"
                        ),
                        did_work=False,
                        error="missing vault_id in marker",
                    )
                else:
                    step_result = fix_fn(
                        vault_path=vault_path,
                        vault_id=vid,
                        name=vault_path.name,
                    )
            else:
                step_result = fix_fn(vault_path)
        except Exception as exc:  # noqa: BLE001 — never let a fix crash doctor
            step_result = setup_steps.StepResult(
                success=False,
                message=f"treatment {result.fix_function} raised: {exc}",
                did_work=False,
                error=str(exc),
            )

        step_results.append(step_result)

    return step_results


# ---------------------------------------------------------------------------
# Filesystem-state hash — used by tests to verify --diagnose is non-mutating
# ---------------------------------------------------------------------------

def vault_state_hash(vault_path: Path) -> str:
    """Return a hash of the vault's full filesystem state.

    Covers file names, contents, mtimes, and sizes. The doctor --diagnose
    flow MUST NOT change this hash between start and end (tests enforce
    this invariant via a before/after comparison).
    """
    sha = hashlib.sha256()
    for p in sorted(vault_path.rglob("*")):
        try:
            rel = p.relative_to(vault_path).as_posix()
            sha.update(rel.encode("utf-8"))
            sha.update(b"\x00")
            if p.is_file():
                sha.update(p.read_bytes())
        except (OSError, ValueError):
            continue
    return sha.hexdigest()
