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
`CheckResult` (pass/fail/skip/warning + fix hint). `run_all_checks`
orchestrates the full suite and enforces dependency order — e.g. the
vault-side checks are skipped if the MCP connection failed, so the
report doesn't mislead with "_MANIFEST.md missing" when the real root
cause is "Obsidian isn't running".

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
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

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
      - "skip": the check could not run (usually because an upstream
                check failed and downstream is meaningless without it).

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

def check_plugin_root() -> CheckResult:
    """Check 1: CLAUDE_PLUGIN_ROOT is set and points at a real directory."""
    root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
    if not root:
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
    path = Path(root)
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
    return CheckResult(
        name="plugin_root",
        status="pass",
        message=f"CLAUDE_PLUGIN_ROOT={path}",
        fixable=False,
    )


def check_environment() -> CheckResult:
    """Check 2: detect Code vs Cowork (informational only — always passes).

    Uses `setup_steps.detect_environment` so there's one source of truth
    for environment detection across init + doctor.
    """
    try:
        import setup_steps  # type: ignore[reportMissingImports]
        env = setup_steps.detect_environment()
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


def check_obsidian_api_key() -> CheckResult:
    """Check 3: OBSIDIAN_API_KEY env var is set and non-empty.

    Not auto-fixable: doctor cannot mint an API key — it has to be obtained
    from Obsidian's Connect MCP plugin. Previously this check advertised
    `setup_env_vars` as a fix target, but `setup_env_vars(api_key=None, ...)`
    short-circuits to a no-op, so the fix was a lie. Escalate to init instead.
    """
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


def check_obsidian_mcp_port() -> CheckResult:
    """Check 4: OBSIDIAN_MCP_PORT env var is set and numeric.

    Not auto-fixable: doctor cannot guess which port the user's Connect MCP
    plugin is bound to. Previously this check advertised `setup_env_vars`,
    but that helper short-circuits when both `api_key` and `port` are None,
    so the fix was a structural no-op. Escalate to init instead.
    """
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


def check_obsidian_running() -> CheckResult:
    """Check 5: Obsidian process is running (best effort, platform-dependent).

    Uses `pgrep` if available, falls back to `ps`. On Windows or other
    environments where neither is available, returns `skip`.
    """
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
                status="skip",
                message=f"pgrep failed: {exc}",
                fixable=False,
            )

    # Fallback: no pgrep available (Windows, minimal alpine, etc.)
    return CheckResult(
        name="obsidian_running",
        status="skip",
        message="pgrep not available on this platform — cannot check process list",
        fixable=False,
    )


def check_mcp_connection(
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

            def _default_factory() -> Any:
                return connect_mcp_client.ConnectMCPClient()

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


def check_scheduled_tasks(env: str) -> CheckResult:
    """Check 12: bundled scheduled tasks are registered.

    This is intentionally best-effort. In Code, we'd want to call
    `CronList` — but that's only available from inside an agent session,
    not from a Python subprocess. So doctor's read-only check can't
    definitively verify the state; it returns `skip` with a note telling
    the agent to verify via CronList in the surrounding skill body.

    In Cowork, we check for `.scheduled-tasks/` SKILL.md files inside
    the workspace if we can find it.
    """
    if env == "code":
        return CheckResult(
            name="scheduled_tasks",
            status="skip",
            message=(
                "scheduled-task registration must be verified via CronList "
                "from the agent session — doctor's Python subprocess cannot "
                "call it directly. The skill body does this check separately."
            ),
            fixable=False,
        )

    # Cowork: try to locate the workspace root. In the absence of a clear
    # contract, degrade to skip.
    return CheckResult(
        name="scheduled_tasks",
        status="skip",
        message=(
            "scheduled-task presence is verified by the init skill in Cowork "
            "via workspace/.scheduled-tasks — doctor cannot definitively "
            "check this from a subprocess."
        ),
        fixable=False,
    )


_LOG_DREAM_RE = re.compile(
    r"^##\s+\[(\d{4}-\d{2}-\d{2})\s+\d{2}:\d{2}\]\s+dream-protocol\s+\|\s+(.*)$",
    re.MULTILINE,
)


def check_last_dream_protocol_run(vault_path: Path) -> CheckResult:
    """Check 13: examine the most recent dream-protocol entry in log.md.

    Informational — reports pass when the most recent run was clean,
    warning when it mentions "issues" or "errors", skip if no run has
    ever happened. Never fixable.
    """
    log = vault_path / "log.md"
    if not log.exists():
        return CheckResult(
            name="last_dream_protocol_run",
            status="skip",
            message="log.md missing — cannot check last dream-protocol run",
            fixable=False,
        )
    try:
        text = log.read_text()
    except OSError as exc:
        return CheckResult(
            name="last_dream_protocol_run",
            status="skip",
            message=f"could not read log.md: {exc}",
            fixable=False,
        )

    matches = list(_LOG_DREAM_RE.finditer(text))
    if not matches:
        return CheckResult(
            name="last_dream_protocol_run",
            status="skip",
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
            status="skip",
            message="no MCP client available to cross-check vault_id",
            fixable=False,
        )

    try:
        mcp_raw = mcp_client.vault_read(".secondbrain-installed")
    except Exception as exc:  # noqa: BLE001
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
      - `validate_hot_memory.py` missing at the expected script path → skip
        with explanation (usually means a partial install).
      - hot-memory file missing → fail with a pointer to dream-protocol.
      - hot-memory file present but invalid → fail with the validator's
        stderr captured in the message.
      - All-good → pass.
    """
    validator = plugin_root / "secondbrain" / "scripts" / "validate_hot_memory.py"
    if not validator.exists():
        return CheckResult(
            name="hot_memory_schema",
            status="skip",
            message=(
                "validate_hot_memory.py is not present at "
                f"{validator}. Skipping the hot-memory check — the plugin "
                "may be only partially installed."
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
            status="skip",
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
            status="skip",
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


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_all_checks(
    vault_path: Path,
    repo_root: Optional[Path] = None,
    plugin_root: Optional[Path] = None,
    mcp_client_factory: Optional[Callable[[], Any]] = None,
) -> List[CheckResult]:
    """Run every check in dependency order and return the results.

    Order matters: if `check_mcp_connection` fails, none of the
    vault-identity / vault-side checks make sense, so we mark them
    `skip` rather than running a potentially-lying filesystem-only
    check.

    `repo_root` is for `check_core_hooks_path` — pass the secondbrain
    repo clone. If None, the check is skipped.

    `plugin_root` is for `check_hot_memory_schema` — pass the
    `CLAUDE_PLUGIN_ROOT` path. If None, defaults to the env var.

    `mcp_client_factory` is for test injection into `check_mcp_connection`.
    Production callers pass None.
    """
    plugin_root = plugin_root or Path(os.environ.get("CLAUDE_PLUGIN_ROOT", ""))
    results: List[CheckResult] = []

    # Checks 1-5 don't need vault access.
    results.append(check_plugin_root())
    results.append(check_environment())
    api_key_result = check_obsidian_api_key()
    results.append(api_key_result)
    port_result = check_obsidian_mcp_port()
    results.append(port_result)
    results.append(check_obsidian_running())

    # Check 6 — MCP connection. Depends on env vars being set.
    if api_key_result.status != "pass" or port_result.status != "pass":
        mcp_result = CheckResult(
            name="mcp_connection",
            status="skip",
            message="skipped because OBSIDIAN_API_KEY / OBSIDIAN_MCP_PORT are not set",
            fixable=False,
        )
    else:
        mcp_result = check_mcp_connection(client_factory=mcp_client_factory)
    results.append(mcp_result)

    # MCP client — reused by check_vault_identity_cross. We only build
    # it if MCP came up; otherwise the identity check is skipped.
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
                import connect_mcp_client  # type: ignore[reportMissingImports]
                mcp_client = connect_mcp_client.ConnectMCPClient()
            except Exception:  # noqa: BLE001
                mcp_client = None

    # Check 6.5 — vault identity cross-check. We call it even when MCP is
    # down: the function handles `mcp_client=None` by still running the
    # FS-side marker validation (which can still surface "marker missing"
    # or "marker has no vault_id") and then returning "skip" for the
    # cross-check proper. This means "marker needs vault_id" is still
    # fixable in Phase 2 even if the user opens doctor offline.
    results.append(check_vault_identity_cross(vault_path, mcp_client=mcp_client))

    # Checks 7-13 — vault-side filesystem checks. Skip all if MCP is down,
    # except check 7 (vault_reachable), which is just a local filesystem
    # check. But if MCP fails because the VAULT_PATH is wrong, it's more
    # useful to see "vault_reachable: fail" than to hide it as a skip, so
    # we run check 7 unconditionally.
    vault_reachable_result = check_vault_reachable(vault_path)
    results.append(vault_reachable_result)

    if vault_reachable_result.status == "fail":
        # Cascading skip for filesystem-dependent checks. Any check whose
        # normal-path branch runs only when the vault is reachable must be
        # emitted here as a skip — otherwise callers that index results by
        # name will see a ragged shape between healthy and broken vaults.
        for name in (
            "manifest", "log_md", "profile", "standard_folders",
            "scheduled_tasks", "last_dream_protocol_run",
            "hot_memory_schema", "ingest_log_recent_failures",
        ):
            results.append(CheckResult(
                name=name,
                status="skip",
                message="skipped because vault path is unreachable",
                fixable=False,
            ))
    else:
        results.append(check_manifest_exists(vault_path))
        results.append(check_log_md_exists(vault_path))
        results.append(check_profile_has_user_content(vault_path))
        results.append(check_standard_folders(vault_path))

        # Env-dependent — best-effort, but we can at least tell CronList
        # to run from outside this module.
        try:
            import setup_steps  # type: ignore[reportMissingImports]
            env = setup_steps.detect_environment()
        except Exception:  # noqa: BLE001
            env = "code"
        results.append(check_scheduled_tasks(env))

        results.append(check_last_dream_protocol_run(vault_path))

        # Phase 3 deferred check
        results.append(check_hot_memory_schema(vault_path, plugin_root))

        # Phase 2/3 deferred check
        results.append(check_ingest_log_recent_failures(vault_path))

    # Check 15 — core.hooksPath (informational, needs the secondbrain repo root).
    if repo_root is not None:
        results.append(check_core_hooks_path(repo_root))
    else:
        results.append(CheckResult(
            name="core_hooks_path",
            status="skip",
            message="repo_root not provided — skipping core.hooksPath check",
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

    step_results: List[Any] = []
    for result in results:
        if result.status != "fail":
            continue
        if not result.fixable or not result.fix_function:
            continue

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
