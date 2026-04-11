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
# T13: dispatch secondbrain-ingester subagent here
#
# Phase 3 (Theme 3) will add the ingester dispatch at this point. The
# ingester runs asynchronously to consume the session transcript and merge
# new turns into hot memory. It must NOT block the Stop hook (the current
# synchronous work above is already on the fast path), so T13 will use
#     nohup ... & disown
# with stdout/stderr redirected to $VAULT/.secondbrain/ingest-log.md so the
# user's session continues immediately after the commit.
# ---------------------------------------------------------------------------

exit 0
