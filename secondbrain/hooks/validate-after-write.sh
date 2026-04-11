#!/usr/bin/env bash
# validate-after-write.sh — PostToolUse hook that validates vault integrity
# after any vault-mutating tool call.
#
# Two code paths:
#
#   MCP vault write (mcp__obsidian__vault_*)
#     Always run verify_vault.py.
#
#   Bash
#     Only run verify_vault.py if the command invoked a *vault-touching*
#     sanctioned Python script. Non-vault-touching sanctioned scripts
#     (bump_version.py, setup_steps.py, ...) and unrelated Bash commands
#     (ls, git, pytest, ...) are skipped — they don't mutate vault state
#     so re-running verify after them would be wasteful.
#
# Exit 0  → allow
# Exit 2  → block + JSON payload telling Claude Code to stop the agent

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VAULT="${VAULT_PATH:-$HOME/cowork}"

if [ ! -d "$VAULT" ]; then
    exit 0  # No vault found, skip validation
fi

VERIFY="$SCRIPT_DIR/scripts/verify_vault.py"
if [ ! -f "$VERIFY" ]; then
    exit 0  # Script not found, skip
fi

# Read the tool payload from stdin into an env var so the Python dispatcher
# below can parse it without fighting the heredoc.
SECONDBRAIN_HOOK_INPUT=$(cat)
export SECONDBRAIN_HOOK_INPUT

# Decide whether to run verify. Exit code: 0 = run, 1 = skip.
SHOULD_RUN=$(python3 <<'PY'
import json
import os
import sys

VAULT_TOUCHING_SCRIPTS = {
    "archive_inbox.py",
    "migrate_v2_to_v3.py",
    "archive_contradiction.py",
    "rebuild_manifest.py",
    "update_hot_memory.py",
    "create_entity_stubs.py",
    "init_obsidian.py",
    "vault_git.py",
    "verify_vault.py",
}

raw = os.environ.get("SECONDBRAIN_HOOK_INPUT", "")
try:
    data = json.loads(raw) if raw else {}
except Exception:
    data = {}

if not isinstance(data, dict):
    sys.stdout.write("skip")
    sys.exit(0)

tool_name = data.get("tool_name", "") or ""
tool_input = data.get("tool_input") or {}

# MCP path: always run.
if tool_name.startswith("mcp__obsidian__vault_"):
    sys.stdout.write("run")
    sys.exit(0)

# Bash path: only run for vault-touching sanctioned scripts.
if tool_name == "Bash":
    command = tool_input.get("command", "") or ""
    for script in VAULT_TOUCHING_SCRIPTS:
        if script in command:
            sys.stdout.write("run")
            sys.exit(0)
    sys.stdout.write("skip")
    sys.exit(0)

# Any other tool: skip (this hook shouldn't have fired, but be defensive).
sys.stdout.write("skip")
PY
)

if [ "$SHOULD_RUN" != "run" ]; then
    exit 0
fi

# Run quick validation (errors only, JSON output)
OUTPUT=$(python3 "$VERIFY" "$VAULT" --check wikilinks,entity-stubs,duplicates --json --quiet 2>/dev/null || true)

ERRORS=$(echo "$OUTPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('summary',{}).get('errors',0))" 2>/dev/null || echo "0")

if [ "$ERRORS" -gt 0 ]; then
    # Extract issue messages
    ISSUES=$(echo "$OUTPUT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
for check in d.get('checks', []):
    for issue in check.get('issues', []):
        if issue['severity'] == 'error':
            print(f\"  - {issue['file']}: {issue['message']}\")
" 2>/dev/null || echo "  - Unable to parse issues")

    cat <<EOF
{
  "decision": "block",
  "reason": "Vault validation found $ERRORS error(s) after write",
  "continue": false
}
EOF
    echo "Vault validation failed ($ERRORS errors). Fix these before continuing:" >&2
    echo "$ISSUES" >&2
    exit 2
fi

exit 0
