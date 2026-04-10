#!/usr/bin/env bash
# validate-after-write.sh — PostToolUse hook that validates vault integrity
# after any Obsidian vault write operation.
#
# Receives JSON on stdin from Claude Code with tool_name, tool_input, etc.
# Runs verify_vault.py and blocks the agent if issues are found.

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
