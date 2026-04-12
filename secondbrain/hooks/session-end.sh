#!/usr/bin/env bash
# SessionEnd hook — T13 simplified audit-log writer.
#
# Pre-T13 this hook emitted a `systemMessage` telling the agent to run
# `/secondbrain:session-end`. T13 retired that discipline: the Stop
# hook commits per turn (T9) and the background ingester (T13) keeps
# hot-memory fresh, so there is nothing left for the agent to flush at
# session close.
#
# What remains:
#
#   1. Read the SessionEnd payload (JSON via stdin). session_id is the
#      only field we actually care about.
#   2. Resolve the active vault via ~/.config/secondbrain/vaults.json
#      (or SECONDBRAIN_VAULTS_CONFIG in tests). If there is no active
#      vault — pre-init state — exit 0 silently.
#   3. Append a single audit line to
#      $VAULT/.secondbrain/ingest-log.md tagging the session as ended.
#   4. Run verify_vault.py --json best-effort, piping its output into
#      the same log so it's captured next to the session-end line.
#   5. Exit 0 unconditionally. Hooks must never wedge a session; a
#      verify failure is a signal, not a blocker.
#
# No systemMessage output. stdout is empty. The pre-T13 user-facing
# nag is gone.

set -euo pipefail

# ---------------------------------------------------------------------------
# Read stdin into an env var so the inline Python block can parse it
# without fighting the heredoc. Empty/malformed stdin is a valid state —
# fall through to exit 0.
# ---------------------------------------------------------------------------

SECONDBRAIN_HOOK_INPUT="$(cat 2>/dev/null || true)"
export SECONDBRAIN_HOOK_INPUT

SB_DECISION_FILE="$(mktemp -t sb_session_end.XXXXXX 2>/dev/null || echo /tmp/sb_session_end.$$)"
export SB_DECISION_FILE

cleanup_decision() {
    rm -f "${SB_DECISION_FILE:-}" 2>/dev/null || true
}
trap cleanup_decision EXIT

# ---------------------------------------------------------------------------
# Parse payload + resolve active vault in one Python pass.
#
# Output contract (to $SB_DECISION_FILE):
#
#   Line 1: "SKIP" or "LOG"
#   Line 2 (if LOG): resolved vault path
#   Line 3 (if LOG): session_id (possibly empty string)
# ---------------------------------------------------------------------------

set +e
python3 <<'PY'
import json
import os
import sys
from pathlib import Path


def _write_decision(lines):
    path = os.environ.get("SB_DECISION_FILE", "")
    if not path:
        return
    try:
        with open(path, "w", encoding="utf-8") as fh:
            for line in lines:
                fh.write(line + "\n")
    except Exception:
        pass


def _skip_and_exit():
    _write_decision(["SKIP"])
    sys.exit(0)


def _config_path():
    override = os.environ.get("SECONDBRAIN_VAULTS_CONFIG")
    if override:
        return Path(override)
    return Path.home() / ".config" / "secondbrain" / "vaults.json"


raw = os.environ.get("SECONDBRAIN_HOOK_INPUT", "")

# 1. Parse payload (malformed → silent skip).
try:
    payload = json.loads(raw) if raw else {}
except Exception:
    _skip_and_exit()

if not isinstance(payload, dict):
    _skip_and_exit()

session_id = str(payload.get("session_id") or "")

# 2. Resolve active vault.
cfg = _config_path()
if not cfg.exists():
    _skip_and_exit()

try:
    data = json.loads(cfg.read_text())
except Exception:
    _skip_and_exit()

if not isinstance(data, dict):
    _skip_and_exit()

active_id = data.get("active_vault_id")
if not active_id:
    _skip_and_exit()

active_entry = None
for entry in data.get("vaults", []) or []:
    if isinstance(entry, dict) and entry.get("id") == active_id:
        active_entry = entry
        break

if not active_entry:
    _skip_and_exit()

vault_path_str = active_entry.get("path") or ""
if not vault_path_str:
    _skip_and_exit()

vault_path = Path(vault_path_str)
if not vault_path.exists() or not vault_path.is_dir():
    _skip_and_exit()

_write_decision(["LOG", str(vault_path), session_id])
sys.exit(0)
PY
set -e

# ---------------------------------------------------------------------------
# Parse the decision file. Anything we don't recognize is a silent skip.
# ---------------------------------------------------------------------------

if [ ! -f "$SB_DECISION_FILE" ]; then
    exit 0
fi

DECISION_LINE="$(sed -n '1p' "$SB_DECISION_FILE" 2>/dev/null || echo "")"
if [ "$DECISION_LINE" != "LOG" ]; then
    exit 0
fi

VAULT_PATH_RESOLVED="$(sed -n '2p' "$SB_DECISION_FILE" 2>/dev/null || echo "")"
SESSION_ID="$(sed -n '3p' "$SB_DECISION_FILE" 2>/dev/null || echo "")"

if [ -z "${VAULT_PATH_RESOLVED:-}" ]; then
    exit 0
fi

# ---------------------------------------------------------------------------
# Append the session-end audit line to ingest-log.md. Create the
# .secondbrain/ directory if missing (this hook can fire on a fresh
# vault where no Stop hook has committed yet).
# ---------------------------------------------------------------------------

LOG_DIR="$VAULT_PATH_RESOLVED/.secondbrain"
LOG_PATH="$LOG_DIR/ingest-log.md"
mkdir -p "$LOG_DIR" 2>/dev/null || true
touch "$LOG_PATH" 2>/dev/null || true

TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
printf '%s [session-end] session %s ended\n' "$TIMESTAMP" "$SESSION_ID" >> "$LOG_PATH" 2>/dev/null || true

# ---------------------------------------------------------------------------
# Best-effort verify_vault.py run. We pipe both stdout and stderr into
# the log so the user can grep for verify failures alongside the session-
# end line. Failure here is non-blocking — the hook always exits 0.
# ---------------------------------------------------------------------------

VERIFY_SCRIPT="${CLAUDE_PLUGIN_ROOT:-}/scripts/verify_vault.py"
if [ -f "$VERIFY_SCRIPT" ]; then
    set +e
    python3 "$VERIFY_SCRIPT" "$VAULT_PATH_RESOLVED" --json >> "$LOG_PATH" 2>&1
    set -e
fi

exit 0
