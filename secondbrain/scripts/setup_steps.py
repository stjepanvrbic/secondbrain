#!/usr/bin/env python3
"""
setup_steps.py — shared setup-step primitives for secondbrain.

A stdlib-only, side-effect-free module used by BOTH the init script and the
doctor skill. Every function is idempotent: re-running it must be safe, and
each `StepResult` reports not just success/failure but whether the call
actually changed anything on disk (`did_work`). Doctor needs this distinction
to report "I fixed X" vs "X was already fine".

This module also owns the multi-vault config at
`~/.config/secondbrain/vaults.json` (overridable via `SECONDBRAIN_VAULTS_CONFIG`
for tests), which is the single source of truth for which vaults are managed
by the plugin on this machine. `list_vault_paths_for_hooks()` is the hook's
runtime lookup into that config.

No side effects at import time. No `print()` calls — logging is the caller's
responsibility (init prints its own step summary, doctor formats a table).

Python 3.8+, zero external dependencies.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class StepResult:
    """Result of a single idempotent setup step.

    `success` is the pass/fail bit. `did_work` distinguishes "I made a change"
    (True) from "I observed nothing needed changing" (False) — important so
    doctor can report the delta from its read-only perspective.
    """
    success: bool
    message: str
    did_work: bool
    error: Optional[str] = None


@dataclass
class VaultEntry:
    """A vault registered in ~/.config/secondbrain/vaults.json.

    `with_push` is forward-compat for Phase 2 (vault git remote with optional
    auto-push). Phase 1 reads/writes it through this module but doesn't act
    on it yet.
    """
    id: str
    path: str
    name: str
    role: str
    added_at: str
    with_push: bool = False


# ---------------------------------------------------------------------------
# Canonical vaults.json location
# ---------------------------------------------------------------------------

# Module-level constant — points at the real on-disk location. Tests and
# dev environments override via the SECONDBRAIN_VAULTS_CONFIG env var, which
# is resolved lazily inside `_config_path()` so the override takes effect
# even if the env var is set after import.
VAULTS_CONFIG_PATH = Path.home() / ".config" / "secondbrain" / "vaults.json"


def _config_path() -> Path:
    """Resolve the current vaults.json path, honoring the env override.

    The env var is checked on every call so tests can use `monkeypatch.setenv`
    without having to reload the module.
    """
    override = os.environ.get("SECONDBRAIN_VAULTS_CONFIG")
    if override:
        return Path(override)
    return VAULTS_CONFIG_PATH


# ---------------------------------------------------------------------------
# detect_environment / detect_obsidian
# ---------------------------------------------------------------------------

def detect_environment() -> str:
    """Return 'code' or 'cowork'.

    init_obsidian.py has no existing `detect_environment` helper — the
    convention documented in `references/environments.md` is to probe for the
    `CronList` tool (available as a Python builtin only when the script runs
    inside a Claude Code session). As a second signal, we inspect the
    ${CLAUDE_PLUGIN_ROOT} path shape: Claude Code caches plugins under
    `~/.claude/plugins/cache/...`, whereas Cowork uses
    `~/Library/Application Support/Claude/local-agent-mode-sessions/...`.

    Returns a plain string, not a StepResult — the caller decides how to
    surface it.
    """
    # Signal 1: is CronList in the Python builtins? (Only true when Claude
    # Code injects its tool set into the interpreter — extremely unlikely
    # from subprocess, but check anyway so the in-process case works.)
    try:
        import builtins  # noqa: PLC0415 — lazy, no side effect cost
        if hasattr(builtins, "CronList"):
            return "code"
    except Exception:
        pass

    # Signal 2: plugin root path shape.
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
    if plugin_root:
        if "local-agent-mode-sessions" in plugin_root:
            return "cowork"
        if ".claude/plugins/cache" in plugin_root or ".claude\\plugins\\cache" in plugin_root:
            return "code"

    # Default: in the absence of a Claude Code fingerprint, assume Cowork.
    # Cowork is the more restrictive environment (sandboxed file access, no
    # CronCreate), so callers that need to branch on capabilities get the
    # safer default.
    return "cowork"


def detect_obsidian() -> Optional[Path]:
    """Return the Obsidian install path on this machine, or None.

    Delegates to `init_obsidian.find_obsidian()` so detection stays in one
    place. Returns a plain `Path | None`, not a StepResult — the caller
    decides how to surface the result.
    """
    # Lazy import keeps setup_steps importable without pulling in the full
    # init_obsidian module (and its dependencies on subprocess, urllib, etc.)
    # at module load time — important for the "importable without side
    # effects" invariant.
    try:
        import init_obsidian  # type: ignore[reportMissingImports]
    except ImportError:
        return None

    plat = init_obsidian.detect_platform()
    return init_obsidian.find_obsidian(plat)


# ---------------------------------------------------------------------------
# setup_env_vars — wraps init_obsidian.set_env_vars in a StepResult
# ---------------------------------------------------------------------------

# Mirror init_obsidian's regexes so we can compute `did_work` without having
# to re-import (and to stay robust if init_obsidian is refactored later).
_EXPORT_RE = re.compile(r"^export\s+(OBSIDIAN_API_KEY|OBSIDIAN_MCP_PORT)=", re.MULTILINE)
_FISH_VAR_RE = re.compile(r"^set\s+-gx\s+(OBSIDIAN_API_KEY|OBSIDIAN_MCP_PORT)\s+", re.MULTILINE)


def setup_env_vars(
    api_key: Optional[str],
    port: Optional[int],
    shell_path: Optional[Path] = None,
    dry_run: bool = False,
) -> StepResult:
    """Idempotently add OBSIDIAN_API_KEY / OBSIDIAN_MCP_PORT to the user's shell config.

    Either `api_key` or `port` (or both) may be None — only the provided
    values get written. `shell_path` forces a specific target file (tests use
    this); when None, the user's default shell config is used.

    Must not add duplicate export lines. Re-running on an up-to-date config
    is a no-op (`did_work=False`, `success=True`).
    """
    if api_key is None and port is None:
        return StepResult(
            success=True,
            message="setup_env_vars: nothing to write (both api_key and port are None)",
            did_work=False,
        )

    # Decide the target file + syntax. If the caller pinned a shell_path we
    # trust them (tests do this, and the explicit-path case always stays on
    # the POSIX branch); otherwise consult init_obsidian for the user's
    # default shell. On Windows, detect_shell() returns "powershell" and
    # init_obsidian handles env vars via [Environment]::SetEnvironmentVariable
    # rather than a dotfile, so we delegate to _set_env_vars_powershell.
    if shell_path is None:
        try:
            import init_obsidian  # type: ignore[reportMissingImports]
        except ImportError:
            return StepResult(
                success=False,
                message="setup_env_vars: init_obsidian not importable",
                did_work=False,
                error="init_obsidian module missing",
            )
        shell_name = init_obsidian.detect_shell()

        # Windows: no dotfile — delegate to init's PowerShell helper and wrap
        # the bool return in a StepResult. We can't cheaply compute did_work
        # for setx/SetEnvironmentVariable (they have no "already set" check),
        # so we conservatively report did_work=True on a real write and
        # did_work=False on dry_run.
        if shell_name == "powershell":
            # _set_env_vars_powershell signature: (port, api_key, dry_run=False)
            # Guard against port=None: the powershell helper needs a concrete
            # port, so if the caller didn't supply one we skip the call.
            if port is None:
                return StepResult(
                    success=True,
                    message="setup_env_vars: nothing to write (port is None on powershell)",
                    did_work=False,
                )
            ok = init_obsidian._set_env_vars_powershell(port, api_key, dry_run)
            if not ok:
                return StepResult(
                    success=False,
                    message="setup_env_vars: powershell helper failed",
                    did_work=False,
                    error="init_obsidian._set_env_vars_powershell returned False",
                )
            return StepResult(
                success=True,
                message="setup_env_vars: powershell environment variables set",
                did_work=not dry_run,
            )

        shell_path = init_obsidian.SHELL_CONFIGS.get(shell_name)
        if shell_path is None:
            return StepResult(
                success=False,
                message=f"setup_env_vars: unknown shell '{shell_name}'",
                did_work=False,
                error=f"no config mapping for shell {shell_name}",
            )
    else:
        shell_path = Path(shell_path)

    # Build the lines we'd like to write. Fish uses `set -gx`, everything else
    # uses `export VAR="val"`. We detect fish purely from the filename so
    # tests don't need to monkeypatch the shell detector.
    is_fish = shell_path.name == "config.fish"
    var_re = _FISH_VAR_RE if is_fish else _EXPORT_RE

    desired: List[str] = []
    if is_fish:
        if port is not None:
            desired.append(f"set -gx OBSIDIAN_MCP_PORT {port}")
        if api_key is not None:
            desired.append(f"set -gx OBSIDIAN_API_KEY {api_key}")
    else:
        if port is not None:
            desired.append(f'export OBSIDIAN_MCP_PORT="{port}"')
        if api_key is not None:
            desired.append(f'export OBSIDIAN_API_KEY="{api_key}"')

    # Filter out vars that are already set in the existing file. We only
    # check for the variable NAME, not the value — if someone set a stale
    # value manually we don't overwrite it (init's documented behavior).
    existing: set = set()
    if shell_path.exists():
        try:
            content = shell_path.read_text()
        except OSError as exc:
            return StepResult(
                success=False,
                message=f"setup_env_vars: could not read {shell_path}",
                did_work=False,
                error=str(exc),
            )
        existing = set(var_re.findall(content))

    to_write = [line for line in desired if not any(v in line for v in existing)]

    if not to_write:
        return StepResult(
            success=True,
            message=f"setup_env_vars: already set in {shell_path}",
            did_work=False,
        )

    if dry_run:
        return StepResult(
            success=True,
            message=f"setup_env_vars: would append {len(to_write)} lines to {shell_path}",
            did_work=False,  # dry-run never claims did_work.
        )

    try:
        shell_path.parent.mkdir(parents=True, exist_ok=True)
        with shell_path.open("a") as f:
            f.write("\n# secondbrain — Obsidian MCP connection\n")
            for line in to_write:
                f.write(line + "\n")
    except OSError as exc:
        return StepResult(
            success=False,
            message=f"setup_env_vars: write to {shell_path} failed",
            did_work=False,
            error=str(exc),
        )

    return StepResult(
        success=True,
        message=f"setup_env_vars: wrote {len(to_write)} line(s) to {shell_path}",
        did_work=True,
    )


# ---------------------------------------------------------------------------
# write_vault_id — idempotent UUID in .secondbrain-installed
# ---------------------------------------------------------------------------

def write_vault_id(vault_path: Path) -> StepResult:
    """Ensure the vault's `.secondbrain-installed` marker carries a `vault_id`.

    Separation of concerns: this function does NOT create the marker. The
    marker is init_obsidian's responsibility; this function only stamps an
    ID onto an existing marker. If the marker is missing, we return
    `success=False`.
    """
    marker = vault_path / ".secondbrain-installed"
    if not marker.exists():
        return StepResult(
            success=False,
            message=f"write_vault_id: marker not found at {marker}",
            did_work=False,
            error="marker missing — init must create it first",
        )

    try:
        raw = marker.read_text()
        data = json.loads(raw) if raw.strip() else {}
    except (OSError, json.JSONDecodeError) as exc:
        return StepResult(
            success=False,
            message=f"write_vault_id: could not parse {marker}",
            did_work=False,
            error=str(exc),
        )

    if not isinstance(data, dict):
        return StepResult(
            success=False,
            message="write_vault_id: marker is not a JSON object",
            did_work=False,
            error="expected JSON object at top level",
        )

    existing = data.get("vault_id")
    if isinstance(existing, str) and existing:
        # Validate that it's actually a UUID; if not, treat as missing and
        # regenerate. This protects against manual edits that drop garbage.
        try:
            uuid.UUID(existing)
            return StepResult(
                success=True,
                message=f"vault_id={existing}",
                did_work=False,
            )
        except ValueError:
            # Fall through to regenerate.
            pass

    new_id = str(uuid.uuid4())
    data["vault_id"] = new_id
    try:
        marker.write_text(json.dumps(data, indent=2))
    except OSError as exc:
        return StepResult(
            success=False,
            message=f"write_vault_id: could not write {marker}",
            did_work=False,
            error=str(exc),
        )

    return StepResult(
        success=True,
        message=f"vault_id={new_id}",
        did_work=True,
    )


# ---------------------------------------------------------------------------
# vaults.json I/O helpers
# ---------------------------------------------------------------------------

_EMPTY_CONFIG = {
    "schema_version": 1,
    "vaults": [],
    "active_vault_id": None,
}


def _load_config() -> dict:
    """Read vaults.json. Returns a fresh empty config dict if the file is
    missing. Raises on malformed JSON (we want hard failures on corruption).
    """
    path = _config_path()
    if not path.exists():
        # Return a NEW dict on every call — callers mutate freely.
        return json.loads(json.dumps(_EMPTY_CONFIG))
    try:
        return json.loads(path.read_text())
    except OSError as exc:
        raise OSError(f"could not read vaults config at {path}: {exc}") from exc


def _save_config(data: dict) -> None:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def _entry_from_dict(d: dict) -> VaultEntry:
    return VaultEntry(
        id=d["id"],
        path=d["path"],
        name=d["name"],
        role=d.get("role", "personal"),
        added_at=d.get("added_at", ""),
        with_push=bool(d.get("with_push", False)),
    )


def _entry_to_dict(e: VaultEntry) -> dict:
    return {
        "id": e.id,
        "path": e.path,
        "name": e.name,
        "role": e.role,
        "added_at": e.added_at,
        "with_push": e.with_push,
    }


def add_vault_to_config(
    vault_path: Path,
    vault_id: str,
    name: str,
    role: str = "personal",
) -> StepResult:
    """Add (or update) a vault entry in ~/.config/secondbrain/vaults.json.

    Creates the file + parent directory if missing. If an entry with the
    same `vault_id` already exists, updates path/name/role in place. Sets
    `active_vault_id` to this vault only if no active vault is set yet.
    """
    try:
        data = _load_config()
    except (OSError, json.JSONDecodeError) as exc:
        return StepResult(
            success=False,
            message="add_vault_to_config: config unreadable",
            did_work=False,
            error=str(exc),
        )

    resolved_path = str(vault_path.resolve() if vault_path.exists() else vault_path)
    vaults = data.setdefault("vaults", [])
    changed = False

    # Look for an existing entry with this ID.
    existing_idx = None
    for i, entry in enumerate(vaults):
        if entry.get("id") == vault_id:
            existing_idx = i
            break

    if existing_idx is not None:
        entry = vaults[existing_idx]
        for key, new_value in (("path", resolved_path), ("name", name), ("role", role)):
            if entry.get(key) != new_value:
                entry[key] = new_value
                changed = True
    else:
        new_entry = VaultEntry(
            id=vault_id,
            path=resolved_path,
            name=name,
            role=role,
            added_at=datetime.now().isoformat(timespec="seconds"),
            with_push=False,
        )
        vaults.append(_entry_to_dict(new_entry))
        changed = True

    # Set active if none is set yet.
    if data.get("active_vault_id") is None:
        data["active_vault_id"] = vault_id
        changed = True

    # Ensure schema_version is always present.
    if data.get("schema_version") != 1:
        data["schema_version"] = 1
        changed = True

    if not changed:
        return StepResult(
            success=True,
            message=f"add_vault_to_config: {vault_id} already registered",
            did_work=False,
        )

    try:
        _save_config(data)
    except OSError as exc:
        return StepResult(
            success=False,
            message="add_vault_to_config: write failed",
            did_work=False,
            error=str(exc),
        )

    return StepResult(
        success=True,
        message=f"add_vault_to_config: registered {vault_id} ({name})",
        did_work=True,
    )


def remove_vault_from_config(vault_id: str) -> StepResult:
    """Remove a vault entry. If it was the active vault, reassign active to
    the first remaining entry (or None if the list is now empty).

    Idempotent — removing a non-existent vault is a no-op.
    """
    path = _config_path()
    if not path.exists():
        # Nothing to remove — treat as a successful no-op.
        return StepResult(
            success=True,
            message=f"remove_vault_from_config: no config file at {path}",
            did_work=False,
        )

    try:
        data = _load_config()
    except (OSError, json.JSONDecodeError) as exc:
        return StepResult(
            success=False,
            message="remove_vault_from_config: config unreadable",
            did_work=False,
            error=str(exc),
        )

    vaults = data.get("vaults", [])
    new_vaults = [v for v in vaults if v.get("id") != vault_id]
    changed = len(new_vaults) != len(vaults)

    data["vaults"] = new_vaults

    # Reassign active if needed.
    if data.get("active_vault_id") == vault_id:
        data["active_vault_id"] = new_vaults[0]["id"] if new_vaults else None
        changed = True

    if not changed:
        return StepResult(
            success=True,
            message=f"remove_vault_from_config: {vault_id} not registered",
            did_work=False,
        )

    try:
        _save_config(data)
    except OSError as exc:
        return StepResult(
            success=False,
            message="remove_vault_from_config: write failed",
            did_work=False,
            error=str(exc),
        )

    return StepResult(
        success=True,
        message=f"remove_vault_from_config: removed {vault_id}",
        did_work=True,
    )


def list_configured_vaults() -> List[VaultEntry]:
    """Return all vaults from vaults.json. Empty list if file is missing.

    Raises on malformed JSON — we want corruption to be a hard failure so
    the user notices and fixes it rather than silently losing vault state.
    """
    path = _config_path()
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    return [_entry_from_dict(v) for v in data.get("vaults", [])]


def get_active_vault() -> Optional[VaultEntry]:
    """Return the active VaultEntry or None."""
    path = _config_path()
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    active_id = data.get("active_vault_id")
    if not active_id:
        return None
    for v in data.get("vaults", []):
        if v.get("id") == active_id:
            return _entry_from_dict(v)
    return None


def list_vault_paths_for_hooks() -> List[str]:
    """Return absolute vault paths for the enforce-immutability hooks.

    Hooks need the list of managed vault paths to know which roots to
    protect. Returns an empty list pre-init (no config file yet), so the
    hook behavior on a pristine install is "protect nothing managed by
    secondbrain" — which is correct.
    """
    return [entry.path for entry in list_configured_vaults()]


# ---------------------------------------------------------------------------
# Doctor treatment helpers
#
# These are thin wrappers around init_obsidian primitives, returning
# StepResult so doctor's Phase 2 dispatcher can uniformly report outcomes.
# Each function is idempotent and never mutates a vault that's already in
# the desired state (`did_work=False`).
# ---------------------------------------------------------------------------

def create_log_md(vault_path: Path) -> StepResult:
    """Ensure `${vault_path}/log.md` exists with the canonical header.

    Idempotent — if the file is already present, returns `did_work=False`
    without touching it. Used by doctor to fix a missing log.md without
    having to re-run the whole init flow.
    """
    log_path = vault_path / "log.md"
    if log_path.exists():
        return StepResult(
            success=True,
            message=f"create_log_md: {log_path} already exists",
            did_work=False,
        )

    today = datetime.now().date().isoformat()
    header = (
        "# Log\n\n"
        f"## [{today} 00:00] doctor | log.md created by doctor treatment\n"
        "Created by /secondbrain:doctor after detecting missing log.md.\n"
    )
    try:
        vault_path.mkdir(parents=True, exist_ok=True)
        log_path.write_text(header)
    except OSError as exc:
        return StepResult(
            success=False,
            message="create_log_md: write failed",
            did_work=False,
            error=str(exc),
        )

    return StepResult(
        success=True,
        message=f"create_log_md: wrote {log_path}",
        did_work=True,
    )


def setup_vault_scaffolding(vault_path: Path) -> StepResult:
    """Create the required folder structure and critical files.

    Delegates to `init_obsidian.scaffold_vault`. Returns `did_work=True`
    iff any dirs or files were actually created. Safe to re-run on an
    already-scaffolded vault (scaffold_vault never overwrites).
    """
    try:
        import init_obsidian  # type: ignore[reportMissingImports]
    except ImportError as exc:
        return StepResult(
            success=False,
            message="setup_vault_scaffolding: init_obsidian not importable",
            did_work=False,
            error=str(exc),
        )

    try:
        created = init_obsidian.scaffold_vault(vault_path, dry_run=False)
    except OSError as exc:
        return StepResult(
            success=False,
            message="setup_vault_scaffolding: scaffold failed",
            did_work=False,
            error=str(exc),
        )

    if created == 0:
        return StepResult(
            success=True,
            message="setup_vault_scaffolding: already fully scaffolded",
            did_work=False,
        )

    return StepResult(
        success=True,
        message=f"setup_vault_scaffolding: created {created} item(s)",
        did_work=True,
    )


def rebuild_manifest(vault_path: Path) -> StepResult:
    """Regenerate `${vault_path}/_MANIFEST.md` via rebuild_manifest.py.

    Always runs (manifest rebuild is cheap and idempotent). Returns
    `did_work=True` on success.
    """
    try:
        import init_obsidian  # type: ignore[reportMissingImports]
    except ImportError as exc:
        return StepResult(
            success=False,
            message="rebuild_manifest: init_obsidian not importable",
            did_work=False,
            error=str(exc),
        )

    # init_obsidian.run_rebuild_manifest spawns the rebuild_manifest.py
    # subprocess — keep the same implementation so there's exactly one
    # manifest-rebuild code path in the plugin.
    try:
        ok = init_obsidian.run_rebuild_manifest(vault_path, dry_run=False)
    except (OSError, RuntimeError) as exc:
        return StepResult(
            success=False,
            message="rebuild_manifest: subprocess failed",
            did_work=False,
            error=str(exc),
        )

    if not ok:
        return StepResult(
            success=False,
            message="rebuild_manifest: rebuild_manifest.py returned non-zero",
            did_work=False,
            error="rebuild_manifest.py failed — see stderr",
        )

    return StepResult(
        success=True,
        message="rebuild_manifest: _MANIFEST.md regenerated",
        did_work=True,
    )


def setup_profile(vault_path: Path, interactive: bool = True) -> StepResult:
    """Seed `me/profile.md` from the shipped template.

    If the profile already has user content (no `{{PLACEHOLDER}}` tokens),
    returns `did_work=False`. Otherwise writes the template (or an
    interactive walkthrough in a future enhancement). For now, `interactive`
    is accepted for API symmetry but the implementation only seeds the
    template file — filling in placeholders happens in the init skill.
    """
    del interactive  # Accepted for API symmetry; unused in Phase 1.

    profile = vault_path / "me" / "profile.md"
    if profile.exists():
        content = profile.read_text()
        if "{{" not in content:
            return StepResult(
                success=True,
                message=f"setup_profile: {profile} already has user content",
                did_work=False,
            )

    try:
        import init_obsidian  # type: ignore[reportMissingImports]
    except ImportError as exc:
        return StepResult(
            success=False,
            message="setup_profile: init_obsidian not importable",
            did_work=False,
            error=str(exc),
        )

    try:
        profile.parent.mkdir(parents=True, exist_ok=True)
        template_text = init_obsidian._load_profile_template()
        profile.write_text(template_text)
    except OSError as exc:
        return StepResult(
            success=False,
            message="setup_profile: write failed",
            did_work=False,
            error=str(exc),
        )

    return StepResult(
        success=True,
        message=(
            f"setup_profile: seeded {profile} from template — "
            "fill in placeholders via /secondbrain:init"
        ),
        did_work=True,
    )


def setup_scheduled_tasks(vault_path: Path) -> StepResult:
    """Placeholder for scheduled-task registration.

    The real work is done by the init skill (which can call CronCreate or
    emit `/schedule` commands depending on environment). Doctor can't
    register tasks from a Python subprocess — this function exists so the
    dispatcher has a target to invoke, and it returns a StepResult that
    clearly tells the user to run init instead.
    """
    del vault_path  # No vault state is touched here.
    return StepResult(
        success=False,
        message=(
            "setup_scheduled_tasks: doctor cannot register scheduled tasks "
            "from a subprocess. Run /secondbrain:init to install them "
            "(Code uses CronCreate; Cowork emits /schedule commands)."
        ),
        did_work=False,
        error="scheduled tasks must be registered by the init skill",
    )
