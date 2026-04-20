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


def _resolve_session_id(payload: dict) -> str:
    if not isinstance(payload, dict):
        return ""
    session_id = payload.get("session_id")
    if isinstance(session_id, str):
        return session_id
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
session_id = _resolve_session_id(payload)
sys.stdout.write(vault + "\t" + cwd + "\t" + session_id)
PY
)"

ACTIVE_VAULT="${RESOLVED%%$'\t'*}"
REST="${RESOLVED#*$'\t'}"
CWD_PAYLOAD="${REST%%$'\t'*}"
SESSION_ID_PAYLOAD="${REST#*$'\t'}"

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
    ACTIVE_VAULT="__SECONDBRAIN_UNCONFIGURED__"
fi

if [ ! -f "${EMITTER}" ]; then
    # Broken plugin layout. Still emit SOMETHING parseable.
    printf '%s\n' '{"systemMessage": "secondbrain emitter script missing. Run /secondbrain:doctor."}'
    exit 0
fi

# ---------------------------------------------------------------------------
# If hot-memory.md is older than 12h, kick off refresh_vault_indexes.py in the
# background. Current session still emits the on-disk (possibly stale) file;
# the next SessionStart picks up the fresh data. This decouples freshness
# from dream-protocol — if dream fails, SessionStart self-heals.
# ---------------------------------------------------------------------------

HOT_MEMORY="${ACTIVE_VAULT}/brain/hot-memory.md"
REFRESH_SCRIPT="${CLAUDE_PLUGIN_ROOT}/scripts/refresh_vault_indexes.py"
STALE_THRESHOLD_SEC=43200  # 12 hours

if [ -d "${ACTIVE_VAULT}" ] && [ -f "${REFRESH_SCRIPT}" ]; then
    # stat -f uses BSD semantics (macOS); -c uses GNU (Linux). Try the BSD
    # form first, fall back for Linux. Missing file → treat as "infinitely
    # stale" so the first ever session triggers a regenerate.
    if MTIME=$(stat -f %m "${HOT_MEMORY}" 2>/dev/null); then :
    elif MTIME=$(stat -c %Y "${HOT_MEMORY}" 2>/dev/null); then :
    else MTIME=0
    fi
    NOW=$(date +%s)
    AGE=$((NOW - MTIME))
    if [ "${AGE}" -gt "${STALE_THRESHOLD_SEC}" ]; then
        # Fully detached: stdout and stderr go to the ingest-log via the
        # script itself, and we nohup + background so the hook returns
        # immediately (SessionStart has a 10s budget).
        nohup python3 "${REFRESH_SCRIPT}" --vault "${ACTIVE_VAULT}" \
            >/dev/null 2>&1 &
        disown 2>/dev/null || true
    fi
fi

# ---------------------------------------------------------------------------
# Belt-and-suspenders log rotation: if log.md is >10 MB, kick off rotate_log
# in the background. Dream-protocol owns the nightly age-based rotation;
# this guard protects users whose dream-protocol is broken.
# ---------------------------------------------------------------------------

ROTATE_SCRIPT="${CLAUDE_PLUGIN_ROOT}/scripts/rotate_log.py"
if [ -d "${ACTIVE_VAULT}" ] && [ -f "${ROTATE_SCRIPT}" ]; then
    nohup python3 "${ROTATE_SCRIPT}" --vault "${ACTIVE_VAULT}" \
        --max-age-days 30 --max-size-mb 10 \
        >/dev/null 2>&1 &
    disown 2>/dev/null || true
fi

# ---------------------------------------------------------------------------
# Delegate to emit_hot_memory.py. It always emits a JSON object and exits 0.
# ---------------------------------------------------------------------------

EMIT_ARGS=(--vault "${ACTIVE_VAULT}")
if [ -n "${SESSION_ID_PAYLOAD}" ]; then
    EMIT_ARGS+=(--session-id "${SESSION_ID_PAYLOAD}")
fi
if [ -n "${CWD_PAYLOAD}" ]; then
    EMIT_ARGS+=(--cwd "${CWD_PAYLOAD}")
fi

python3 "${EMITTER}" "${EMIT_ARGS[@]}"

exit 0
