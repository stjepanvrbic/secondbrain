#!/usr/bin/env bash
# Stop hook — fires after every agent turn.
#
# Responsibilities (Phase 2):
#
#   1. Read Stop hook JSON payload from stdin (session_id, transcript_path,
#      cwd, stop_hook_active). Honor stop_hook_active=true as a loop guard —
#      exit 0 immediately so the hook can never re-trigger itself inside a
#      Stop that Claude Code is already processing.
#
#   2. Resolve the active vault via ~/.config/secondbrain/vaults.json (or
#      SECONDBRAIN_VAULTS_CONFIG in tests). If the config is missing or no
#      active vault is set, exit 0 silently — pre-init is a valid state.
#
#   3. If the active vault isn't a git repo (user opted out of git during
#      init), exit 0 silently — git tracking is opt-in.
#
#   4. Otherwise invoke `vault_git.py commit-stop` on the active vault. The
#      --push flag is added iff the vault's `with_push=True` in vaults.json.
#
#   5. Append the commit outcome + an ISO timestamp to
#      $VAULT/.secondbrain/ingest-log.md, creating the directory and file
#      if missing. Error output from vault_git.py is captured into the log
#      so the user's Claude session is never spammed with git noise.
#
#   6. Exit 0 regardless of commit outcome. Hooks must never wedge a session:
#      a commit failure should be logged and walked past, not propagated.
#
# Phase 3 will add: dispatch the secondbrain-ingester subagent after the
# commit lands, so session transcripts get merged into hot memory. That
# logic goes at the END of this script (marked with a T13 comment below).

set -euo pipefail

# ---------------------------------------------------------------------------
# Read stdin payload into an env var so the inline Python block below can
# parse it without fighting the heredoc. Allow empty stdin gracefully — if
# the caller piped nothing, we still exit 0 cleanly.
# ---------------------------------------------------------------------------

SECONDBRAIN_HOOK_INPUT="$(cat 2>/dev/null || true)"
export SECONDBRAIN_HOOK_INPUT

# Scratch file for the Python→shell handoff. Using a file avoids the "run a
# heredoc inside $(...)" pitfall (nested quoting gets hairy fast) and makes
# the decision auditable if the hook misbehaves.
SB_DECISION_FILE="$(mktemp -t sb_on_stop_decision.XXXXXX 2>/dev/null || echo /tmp/sb_on_stop_decision.$$)"
export SB_DECISION_FILE

# Always clean up the decision file; the trap also clears the env var so
# child processes (e.g., vault_git.py) don't inherit a dangling path.
cleanup_decision() {
    rm -f "${SB_DECISION_FILE:-}" 2>/dev/null || true
}
trap cleanup_decision EXIT

# ---------------------------------------------------------------------------
# Single Python invocation for all parsing + config lookup. Running one
# python3 rather than several subprocesses keeps the hook fast (~50ms
# target) and centralizes error handling in one spot. Any exception inside
# this block is swallowed — the hook MUST exit 0 at the bottom.
#
# Output contract (written to $SB_DECISION_FILE):
#
#   Line 1 (always):  "SKIP" or "COMMIT"
#   Line 2 (if COMMIT): resolved vault path
#   Line 3 (if COMMIT): "push" or "nopush"
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
        with open(path, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")
    except Exception:
        pass


def _config_path():
    override = os.environ.get("SECONDBRAIN_VAULTS_CONFIG")
    if override:
        return Path(override)
    return Path.home() / ".config" / "secondbrain" / "vaults.json"


def _skip_and_exit():
    _write_decision(["SKIP"])
    sys.exit(0)


raw = os.environ.get("SECONDBRAIN_HOOK_INPUT", "")

# 1. Parse the hook payload. Malformed → skip silently.
try:
    payload = json.loads(raw) if raw else {}
except Exception:
    _skip_and_exit()

if not isinstance(payload, dict):
    _skip_and_exit()

# 2. stop_hook_active loop guard. If Claude Code is already mid-stop, we
#    must not commit again — that would re-trigger this hook recursively.
if bool(payload.get("stop_hook_active", False)):
    _skip_and_exit()

# 3. Resolve the active vault from vaults.json.
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

# 4. Check whether the vault is a git repo. If not, the user opted out of
#    git tracking during init — silent no-op. `.git` can be a directory
#    (regular repo) or a file (linked worktree); either counts.
git_marker = vault_path / ".git"
if not git_marker.exists():
    _skip_and_exit()

# 5. All checks passed — emit a COMMIT decision with the resolved vault
#    path and whether to pass --push.
with_push = bool(active_entry.get("with_push", False))
_write_decision(["COMMIT", str(vault_path), "push" if with_push else "nopush"])
sys.exit(0)
PY
set -e

# ---------------------------------------------------------------------------
# Parse the decision file. Anything we don't recognize is treated as SKIP
# so a bug in the Python block above can never wedge the session.
# ---------------------------------------------------------------------------

if [ ! -f "$SB_DECISION_FILE" ]; then
    exit 0
fi

DECISION_LINE="$(sed -n '1p' "$SB_DECISION_FILE" 2>/dev/null || echo "")"
if [ "$DECISION_LINE" != "COMMIT" ]; then
    exit 0
fi

VAULT_PATH_RESOLVED="$(sed -n '2p' "$SB_DECISION_FILE" 2>/dev/null || echo "")"
PUSH_FLAG_TAG="$(sed -n '3p' "$SB_DECISION_FILE" 2>/dev/null || echo "nopush")"

if [ -z "${VAULT_PATH_RESOLVED:-}" ]; then
    exit 0
fi

# ---------------------------------------------------------------------------
# Invoke vault_git.py commit-stop and capture its output (stdout + stderr
# merged) so we can append both to ingest-log.md without spamming the
# user's session.
# ---------------------------------------------------------------------------

COMMIT_MESSAGE="Session checkpoint"
VAULT_GIT="${CLAUDE_PLUGIN_ROOT:-}/scripts/vault_git.py"

if [ ! -f "$VAULT_GIT" ]; then
    # Plugin layout is broken — still exit 0 so the session doesn't wedge.
    exit 0
fi

COMMIT_CMD=(
    python3 "$VAULT_GIT" commit-stop
    --vault "$VAULT_PATH_RESOLVED"
    --message "$COMMIT_MESSAGE"
)
if [ "$PUSH_FLAG_TAG" = "push" ]; then
    COMMIT_CMD+=(--push)
fi

set +e
COMMIT_OUTPUT="$("${COMMIT_CMD[@]}" 2>&1)"
COMMIT_RC=$?
set -e

# ---------------------------------------------------------------------------
# Append the outcome to $VAULT/.secondbrain/ingest-log.md. Create the
# directory and file if missing. Use a Python block to handle the append
# so we get a single atomic write rather than multiple shell redirects.
# ---------------------------------------------------------------------------

export SB_INGEST_VAULT="$VAULT_PATH_RESOLVED"
export SB_INGEST_OUTPUT="$COMMIT_OUTPUT"
export SB_INGEST_RC="$COMMIT_RC"
export SB_INGEST_PUSH_TAG="$PUSH_FLAG_TAG"

set +e
python3 <<'PY'
import os
from datetime import datetime
from pathlib import Path

vault = Path(os.environ.get("SB_INGEST_VAULT", ""))
if not vault or not vault.exists():
    raise SystemExit(0)

output = os.environ.get("SB_INGEST_OUTPUT", "")
rc = os.environ.get("SB_INGEST_RC", "0")
push_tag = os.environ.get("SB_INGEST_PUSH_TAG", "nopush")

try:
    log_dir = vault / ".secondbrain"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "ingest-log.md"

    ts = datetime.now().isoformat(timespec="seconds")
    header = f"## [{ts}] on-stop | commit-stop (rc={rc}, push={push_tag})"
    body_lines = (output or "").splitlines() or ["(no output)"]
    body = "\n".join(f"    {line}" for line in body_lines)
    entry = f"{header}\n{body}\n\n"

    with log_path.open("a", encoding="utf-8") as f:
        f.write(entry)
except Exception:
    # Any log-writing failure is a soft error — we never fail the hook on
    # bookkeeping. The commit already happened (or failed loudly); what
    # the log says about it is secondary.
    pass
PY
set -e

# ---------------------------------------------------------------------------
# T13: Dispatch the secondbrain-ingester subagent.
#
# After the per-turn commit lands, we run extract_new_turns.py to build a
# context envelope at /tmp/secondbrain-stop-context-<session>.json. If the
# envelope has any new turns, we spawn `claude --agent secondbrain-ingester`
# in the background via `nohup ... & disown`. The ingester consumes the
# envelope, routes new content into the vault, updates hot-memory, and
# commits the result — all without blocking the user's session.
#
# Gates (any of these short-circuit the dispatch path, but NEVER crash
# the hook):
#
#   - new_turns count == 0             → nothing to ingest, skip
#   - SECONDBRAIN_SKIP_INGESTER_DISPATCH=1 → test-friendly opt-out
#   - `claude` CLI not on PATH         → graceful degrade, log, skip
#   - extract_new_turns.py fails       → log error, skip dispatch
#
# All dispatch decisions + errors append to ingest-log.md so `tail -f
# ingest-log.md` is the single audit trail for background ingest.
# ---------------------------------------------------------------------------

# Re-parse the hook payload one more time to lift out the session id,
# transcript path, and cwd. Doing this in its own python invocation is
# cheap (<10ms) and keeps the Phase 2 commit block above completely
# untouched — the Phase 2 path is load-bearing and we don't want to
# thread new variables through it.
SB_PHASE3_VARS="$(mktemp -t sb_on_stop_phase3.XXXXXX 2>/dev/null || echo /tmp/sb_on_stop_phase3.$$)"
export SB_PHASE3_VARS

cleanup_phase3() {
    rm -f "${SB_PHASE3_VARS:-}" 2>/dev/null || true
}
# Chain onto the existing EXIT trap without clobbering cleanup_decision.
trap 'cleanup_decision; cleanup_phase3' EXIT

set +e
python3 <<'PY'
import json
import os
import sys

raw = os.environ.get("SECONDBRAIN_HOOK_INPUT", "")
out_path = os.environ.get("SB_PHASE3_VARS", "")

def _write(values):
    if not out_path:
        return
    try:
        with open(out_path, "w", encoding="utf-8") as fh:
            for line in values:
                fh.write(line + "\n")
    except Exception:
        pass

try:
    payload = json.loads(raw) if raw else {}
except Exception:
    _write(["", "", ""])
    sys.exit(0)

if not isinstance(payload, dict):
    _write(["", "", ""])
    sys.exit(0)

session_id = str(payload.get("session_id") or "")
transcript_path = str(payload.get("transcript_path") or "")
cwd_value = str(payload.get("cwd") or "")
_write([session_id, transcript_path, cwd_value])
PY
set -e

SESSION_ID=""
TRANSCRIPT_PATH=""
CWD_VALUE=""
if [ -f "$SB_PHASE3_VARS" ]; then
    SESSION_ID="$(sed -n '1p' "$SB_PHASE3_VARS" 2>/dev/null || echo "")"
    TRANSCRIPT_PATH="$(sed -n '2p' "$SB_PHASE3_VARS" 2>/dev/null || echo "")"
    CWD_VALUE="$(sed -n '3p' "$SB_PHASE3_VARS" 2>/dev/null || echo "")"
fi

# If we couldn't lift a session id, bail out of the dispatch path.
# Everything Phase 2 needed already happened above.
if [ -z "${SESSION_ID:-}" ]; then
    exit 0
fi

LOG_PATH="$VAULT_PATH_RESOLVED/.secondbrain/ingest-log.md"
# Ensure the log dir exists (Phase 2 usually created it already, but
# belt-and-braces — dispatch runs even if Phase 2 had nothing to commit).
mkdir -p "$(dirname "$LOG_PATH")" 2>/dev/null || true
touch "$LOG_PATH" 2>/dev/null || true

_phase3_log() {
    local msg="$1"
    local ts
    ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf '%s [on-stop] %s\n' "$ts" "$msg" >> "$LOG_PATH" 2>/dev/null || true
}

# Run extract_new_turns.py to build the envelope. A failure here is
# non-fatal — we log it and skip dispatch.
ENVELOPE="/tmp/secondbrain-stop-context-${SESSION_ID}.json"
EXTRACT_SCRIPT="${CLAUDE_PLUGIN_ROOT:-}/scripts/extract_new_turns.py"

if [ ! -f "$EXTRACT_SCRIPT" ]; then
    _phase3_log "extract_new_turns.py missing; skipping ingest dispatch for $SESSION_ID"
    exit 0
fi

# Use the parsed cwd if we got one, else fall back to the vault path.
if [ -z "${CWD_VALUE:-}" ]; then
    CWD_VALUE="$VAULT_PATH_RESOLVED"
fi

set +e
python3 "$EXTRACT_SCRIPT" \
    --session "$SESSION_ID" \
    --transcript "$TRANSCRIPT_PATH" \
    --vault "$VAULT_PATH_RESOLVED" \
    --cwd "$CWD_VALUE" \
    --output "$ENVELOPE" >> "$LOG_PATH" 2>&1
EXTRACT_RC=$?
set -e

if [ "$EXTRACT_RC" -ne 0 ]; then
    _phase3_log "extract_new_turns failed (rc=$EXTRACT_RC) for session $SESSION_ID"
    exit 0
fi

# Test-friendly escape hatch: SECONDBRAIN_SKIP_INGESTER_DISPATCH=1 stops
# us from spawning the subagent even when new_turns > 0. Used by unit
# tests that want to exercise the extract path without paying for an
# actual background subprocess.
if [ "${SECONDBRAIN_SKIP_INGESTER_DISPATCH:-}" = "1" ]; then
    _phase3_log "SECONDBRAIN_SKIP_INGESTER_DISPATCH=1; skipping dispatch for $SESSION_ID"
    exit 0
fi

# Parse the new_turns count out of the envelope. If the envelope is
# malformed, treat it as zero and skip dispatch — the error will have
# already been captured by extract_new_turns.py above.
set +e
NEW_TURNS="$(python3 -c "
import json, sys
try:
    with open('$ENVELOPE', 'r', encoding='utf-8') as fh:
        data = json.load(fh)
    turns = data.get('new_turns', [])
    if isinstance(turns, list):
        print(len(turns))
    else:
        print(0)
except Exception:
    print(0)
" 2>/dev/null)"
set -e

if [ -z "${NEW_TURNS:-}" ]; then
    NEW_TURNS=0
fi

if [ "$NEW_TURNS" -le 0 ] 2>/dev/null; then
    _phase3_log "no new turns for $SESSION_ID; skipping ingest dispatch"
    exit 0
fi

# Graceful degrade: without `claude` on PATH, we can't dispatch. Log it
# and exit cleanly — Phase 2 already committed, that's the important part.
if ! command -v claude >/dev/null 2>&1; then
    _phase3_log "\`claude\` CLI not on PATH; skipping ingest dispatch for $SESSION_ID ($NEW_TURNS new turns)"
    exit 0
fi

# Dispatch the detached ingester. nohup + disown means the subprocess
# survives the parent Stop-hook process death and the user's terminal
# can close without aborting the ingest. stdout/stderr pipe into the
# same ingest-log.md so the user has a single audit trail.
DISPATCH_PROMPT="Process the secondbrain stop context envelope at $ENVELOPE. Session: $SESSION_ID."

nohup claude --agent secondbrain-ingester -p "$DISPATCH_PROMPT" \
    >> "$LOG_PATH" 2>&1 &
disown $! 2>/dev/null || true

_phase3_log "dispatched ingester for $SESSION_ID ($NEW_TURNS new turns)"

exit 0
