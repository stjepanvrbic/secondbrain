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
    *,
    with_push: bool = False,
) -> StepResult:
    """Add (or update) a vault entry in ~/.config/secondbrain/vaults.json.

    Creates the file + parent directory if missing. If an entry with the
    same `vault_id` already exists, updates path/name/role/with_push in
    place. Sets `active_vault_id` to this vault only if no active vault is
    set yet.

    `with_push` is forward-compat for Phase 2 (Stop hook auto-push). When
    True, T9's Stop hook will push after every commit. The flag is
    persisted here so all callers — init, doctor, the vault-management CLI
    — share a single source of truth. Flipping the flag on an existing
    entry counts as `did_work=True` so doctor can observe the change.
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
        updates = (
            ("path", resolved_path),
            ("name", name),
            ("role", role),
            ("with_push", with_push),
        )
        for key, new_value in updates:
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
            with_push=with_push,
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


# ---------------------------------------------------------------------------
# setup_git — Phase 2 init→git integration
#
# setup_git is called from the init skill's Step 4a AND by doctor treatment
# when a managed vault's git state has drifted (missing .gitignore, no
# initial commit, etc.). Like every other setup_steps helper it's idempotent
# and returns StepResult so the caller can uniformly report "already set
# up" vs "just fixed".
#
# Why this lives in setup_steps and not vault_git: the composition
# (init → gitignore → initial commit → optional remote → optional push)
# is setup policy, not a git primitive. vault_git owns the primitives;
# setup_steps owns the order those primitives run in during init/doctor.
# ---------------------------------------------------------------------------

def setup_git(
    vault_path: Path,
    *,
    with_remote: bool = False,
    remote_url: Optional[str] = None,
    with_push: bool = False,
    dry_run: bool = False,
) -> StepResult:
    """Idempotent git setup for a vault.

    Runs each step of the T8 init→git flow in order, tracking whether any
    step actually changed state. Returns success=True if the vault is now
    a git repo with at least one commit.

    Steps (each delegated to a vault_git helper):

      1. If not already a git repo, run `vault_git.init_repo()`
      2. Write the default `.gitignore` via `vault_git.write_gitignore()`
      3. If the repo has zero commits, run `vault_git.initial_commit()`
      4. If `with_remote=True` AND `remote_url` is provided, add an `origin`
         remote pointing at it (via `vault_git.init_repo(with_remote=True,
         remote_url=...)` re-invoked on an already-initialized repo — or
         added directly via subprocess when the repo existed before us)
      5. If `with_remote` AND `with_push`, run `git push -u origin HEAD`
         and treat a push failure as a soft warning — local commits are
         still durable, so we leave `success=True` and record the push
         error in `message`.

    `dry_run=True` makes every step a no-op but still reports what would
    have happened.

    Nothing about this function writes to `vaults.json`. That's the init
    skill's responsibility (via `add_vault_to_config(..., with_push=...)`)
    and keeps setup_steps from having to know which vault_id corresponds
    to `vault_path`. setup_git is a local-state operator; vaults.json is
    global state.
    """
    # Lazy import so setup_steps stays importable without dragging the
    # full vault_git module (which pulls subprocess + shutil at top level)
    # into any consumer that never touches git. Deliberate: keeps doctor
    # and init_obsidian's import surface small.
    from vault_git import (  # type: ignore[reportMissingImports]  # noqa: PLC0415
        init_repo,
        initial_commit,
        is_git_repo,
        write_gitignore,
    )

    # Pre-flight: vault must exist.
    if not vault_path.exists():
        return StepResult(
            success=False,
            message=f"setup_git: vault path does not exist: {vault_path}",
            did_work=False,
            error=f"vault path does not exist: {vault_path}",
        )
    if not vault_path.is_dir():
        return StepResult(
            success=False,
            message=f"setup_git: vault path is not a directory: {vault_path}",
            did_work=False,
            error=f"vault path is not a directory: {vault_path}",
        )

    did_any_work = False
    push_warning: Optional[str] = None

    # Step 1: init_repo (unless already a repo).
    #
    # We pass with_remote/remote_url through to init_repo so a fresh init
    # and a remote-add land in a single call. For an already-initialized
    # repo init_repo short-circuits and we handle the remote separately
    # below so we still get the opt-in behavior.
    already_repo = is_git_repo(vault_path)
    if not already_repo:
        init_result = init_repo(
            vault_path,
            with_remote=with_remote,
            remote_url=remote_url if with_remote else None,
            dry_run=dry_run,
        )
        if not init_result.success:
            return StepResult(
                success=False,
                message=f"setup_git: init_repo failed: {init_result.message}",
                did_work=init_result.did_work,
                error=init_result.error,
            )
        did_any_work = did_any_work or init_result.did_work

    # Step 2: write_gitignore (always run — it's idempotent).
    gi_result = write_gitignore(vault_path, dry_run=dry_run)
    if not gi_result.success:
        return StepResult(
            success=False,
            message=f"setup_git: write_gitignore failed: {gi_result.message}",
            did_work=did_any_work or gi_result.did_work,
            error=gi_result.error,
        )
    did_any_work = did_any_work or gi_result.did_work

    # Step 3: initial_commit if zero commits exist.
    #
    # initial_commit short-circuits cleanly on a repo that already has
    # commits (returns success=True, did_work=False), so we can call it
    # unconditionally without clobbering user history.
    if not dry_run:
        ic_result = initial_commit(vault_path)
        if not ic_result.success:
            return StepResult(
                success=False,
                message=f"setup_git: initial_commit failed: {ic_result.message}",
                did_work=did_any_work or ic_result.did_work,
                error=ic_result.error,
            )
        did_any_work = did_any_work or ic_result.did_work

    # Step 4: if the repo existed before we got here AND the caller asked
    # for a remote, add origin directly. init_repo only adds the remote on
    # a fresh init (deliberate: it doesn't clobber existing remote config),
    # so we handle the pre-existing-repo case here. Missing URL is a no-op.
    if (
        with_remote
        and remote_url
        and already_repo
        and not dry_run
    ):
        import shutil  # noqa: PLC0415 — lazy, avoid top-level dep cost
        import subprocess  # noqa: PLC0415

        if shutil.which("git") is None:
            return StepResult(
                success=False,
                message="setup_git: git not installed",
                did_work=did_any_work,
                error="git binary not found on PATH",
            )

        # Only add origin if it doesn't already exist — we never clobber
        # the user's existing remote config.
        check = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(vault_path),
            capture_output=True,
            text=True,
            check=False,
        )
        if check.returncode != 0:
            add = subprocess.run(
                ["git", "remote", "add", "origin", remote_url],
                cwd=str(vault_path),
                capture_output=True,
                text=True,
                check=False,
            )
            if add.returncode != 0:
                return StepResult(
                    success=False,
                    message="setup_git: git remote add origin failed",
                    did_work=did_any_work,
                    error=(add.stderr or add.stdout).strip() or "git remote add failed",
                )
            did_any_work = True

    # Step 5: optional push. Fail-soft — we prefer a durable local commit
    # over a hard failure on a flaky remote. The push error goes into the
    # returned message so callers/tests can observe it, but success stays
    # True.
    if with_remote and remote_url and with_push and not dry_run:
        import shutil  # noqa: PLC0415
        import subprocess  # noqa: PLC0415

        if shutil.which("git") is None:
            return StepResult(
                success=False,
                message="setup_git: git not installed",
                did_work=did_any_work,
                error="git binary not found on PATH",
            )

        push = subprocess.run(
            ["git", "push", "-u", "origin", "HEAD"],
            cwd=str(vault_path),
            capture_output=True,
            text=True,
            check=False,
        )
        if push.returncode != 0:
            push_warning = (push.stderr or push.stdout).strip() or "git push failed"
        else:
            did_any_work = True

    # Final status message.
    if dry_run:
        return StepResult(
            success=True,
            message=f"setup_git: would initialize git in {vault_path}",
            did_work=False,
        )

    if push_warning is not None:
        return StepResult(
            success=True,  # local state is good; push is the soft failure
            message=(
                f"setup_git: initialized {vault_path} "
                f"(push failed: {push_warning})"
            ),
            did_work=did_any_work,
        )

    return StepResult(
        success=True,
        message=f"setup_git: initialized {vault_path}",
        did_work=did_any_work,
    )
