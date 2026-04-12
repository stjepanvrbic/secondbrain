#!/usr/bin/env bash
# SessionStart hook — T11.
#
# Reads the Claude Code SessionStart payload from stdin, resolves the
# active vault from ~/.config/secondbrain/vaults.json (or
# SECONDBRAIN_VAULTS_CONFIG in tests), and delegates to emit_hot_memory.py
# to produce the `{"systemMessage": "..."}` JSON the hook returns.
#
# Fast by design — this runs on every SessionStart (startup/clear/compact),
# so the python handoff must stay cheap. No MCP, no network, no heavy
# imports.
#
# Failure-mode contract: the hook ALWAYS emits a parseable JSON object
# on stdout and ALWAYS exits 0. A broken hook = a broken session.

set -euo pipefail

# ---------------------------------------------------------------------------
# Resolve the plugin root. Claude Code sets CLAUDE_PLUGIN_ROOT for hooks,
# but the tests invoke the hook directly — default to script's parent dir.
# ---------------------------------------------------------------------------

if [ -z "${CLAUDE_PLUGIN_ROOT:-}" ]; then
    HOOK_DIR="$(cd -- "$(dirname -- "$0")" &> /dev/null && pwd)"
    CLAUDE_PLUGIN_ROOT="$(cd -- "${HOOK_DIR}/.." &> /dev/null && pwd)"
    export CLAUDE_PLUGIN_ROOT
fi

EMITTER="${CLAUDE_PLUGIN_ROOT}/scripts/emit_hot_memory.py"

# ---------------------------------------------------------------------------
# Read the hook payload from stdin. Empty / malformed stdin is fine — we
# still need to run and at least emit a fallback.
# ---------------------------------------------------------------------------

SECONDBRAIN_HOOK_INPUT="$(cat 2>/dev/null || true)"
export SECONDBRAIN_HOOK_INPUT

# ---------------------------------------------------------------------------
# Single python block: parse stdin, resolve active vault, print a tab-
# separated "<vault_path>\t<cwd>" line (either can be empty). Using one
# python process rather than two keeps the hook fast.
# ---------------------------------------------------------------------------

RESOLVED="$(
python3 - <<'PY'
import json
import os
import sys
from pathlib import Path


def _config_path() -> Path:
    override = os.environ.get("SECONDBRAIN_VAULTS_CONFIG")
    if override:
        return Path(override)
    return Path.home() / ".config" / "secondbrain" / "vaults.json"


def _resolve_cwd(payload: dict) -> str:
    if not isinstance(payload, dict):
        return ""
    cwd = payload.get("cwd")
    if isinstance(cwd, str):
        return cwd
    return ""


def _resolve_vault() -> str:
    cfg = _config_path()
    if not cfg.exists():
        return ""
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    active_id = data.get("active_vault_id")
    if not active_id:
        return ""
    for entry in data.get("vaults", []) or []:
        if isinstance(entry, dict) and entry.get("id") == active_id:
            path = entry.get("path") or ""
            if isinstance(path, str):
                return path
    return ""


raw = os.environ.get("SECONDBRAIN_HOOK_INPUT", "")
try:
    payload = json.loads(raw) if raw else {}
except Exception:
    payload = {}

vault = _resolve_vault()
cwd = _resolve_cwd(payload)
sys.stdout.write(vault + "\t" + cwd)
PY
)"

ACTIVE_VAULT="${RESOLVED%%$'\t'*}"
CWD_PAYLOAD="${RESOLVED#*$'\t'}"

# ---------------------------------------------------------------------------
# No active vault → pre-init state. Emit a fallback so the agent knows
# something is up and exit 0.
# ---------------------------------------------------------------------------

# Fallback: if vaults.json didn't resolve a vault, check if the cwd
# contains _MANIFEST.md (common in Cowork where the workspace IS the vault).
if [ -z "${ACTIVE_VAULT}" ] && [ -n "${CWD_PAYLOAD}" ] && [ -f "${CWD_PAYLOAD}/_MANIFEST.md" ]; then
    ACTIVE_VAULT="${CWD_PAYLOAD}"
fi

if [ -z "${ACTIVE_VAULT}" ]; then
    printf '%s\n' '{"systemMessage": "secondbrain not configured. Run /secondbrain:init to set up."}'
    exit 0
fi

# ---------------------------------------------------------------------------
# Append a session-start entry to log.md so update_hot_memory.py's
# "Recent Activity" reflects actual session activity. Without this,
# the agent reports "N days without session" because only dream-protocol
# writes to log.md (nightly), not sessions.
# ---------------------------------------------------------------------------

set +e
python3 - "${ACTIVE_VAULT}" <<'PY'
import sys
from datetime import datetime
from pathlib import Path

vault = Path(sys.argv[1]) if len(sys.argv) > 1 else None
if not vault or not vault.is_dir():
    sys.exit(0)

log = vault / "log.md"
ts = datetime.now().strftime("%Y-%m-%d %H:%M")
entry = f"\n## [{ts}] session-activity | checkpoint\n"

try:
    if not log.exists():
        log.write_text(f"# Log\n{entry}", encoding="utf-8")
    else:
        with log.open("a", encoding="utf-8") as f:
            f.write(entry)
except Exception:
    pass
PY
set -e

if [ ! -f "${EMITTER}" ]; then
    # Broken plugin layout. Still emit SOMETHING parseable.
    printf '%s\n' '{"systemMessage": "secondbrain emitter script missing. Run /secondbrain:doctor."}'
    exit 0
fi

# ---------------------------------------------------------------------------
# Delegate to emit_hot_memory.py. It always emits a JSON object and exits 0.
# ---------------------------------------------------------------------------

if [ -n "${CWD_PAYLOAD}" ]; then
    python3 "${EMITTER}" --vault "${ACTIVE_VAULT}" --cwd "${CWD_PAYLOAD}"
else
    python3 "${EMITTER}" --vault "${ACTIVE_VAULT}"
fi

exit 0
