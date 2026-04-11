#!/usr/bin/env bash
# PreToolUse hook — enforces inbox/ and archive/ immutability for Bash commands.
#
# Companion to enforce-immutability.sh (which covers MCP tools).
# This hook catches Bash commands that would modify inbox/ or archive/.
#
# Allowed:
#   - Read operations (ls, cat, grep, find, head, tail, less, wc, file)
#   - Invocations of our sanctioned scripts (archive_inbox.py, migrate_v2_to_v3.py)
#
# Blocked:
#   - Any write verb (mv, rm, cp, touch, sed -i, tee, echo/printf redirection)
#     that references a path containing inbox/ or archive/
#
# Exit 0 → allow
# Exit 2 → block with stderr message

set -euo pipefail

input=$(cat)

# Extract command from tool_input.command
command=$(python3 -c "
import sys, json
try:
    data = json.loads(sys.stdin.read())
    print(data.get('tool_input', {}).get('command', ''))
except Exception:
    print('')
" <<< "$input")

if [ -z "$command" ]; then
    exit 0
fi

# Fast path: if command doesn't mention inbox/ or archive/, allow
if ! echo "$command" | grep -qE '(^|[^a-zA-Z0-9_-])(inbox|archive)/'; then
    exit 0
fi

# Allow invocations of our sanctioned scripts
if echo "$command" | grep -qE '(archive_inbox\.py|migrate_v2_to_v3\.py)'; then
    exit 0
fi

# Allow pure read operations. Check for any write verb.
# Write verbs: mv, rm, cp, touch, tee, sed -i, redirection >
if echo "$command" | grep -qE '(^|[;&|` ])(mv|rm|cp|touch|tee|dd|install)([ \t]|$)'; then
    block_reason="write command targeting inbox/ or archive/"
elif echo "$command" | grep -qE 'sed[[:space:]]+-i'; then
    block_reason="sed -i targeting inbox/ or archive/"
elif echo "$command" | grep -qE '>[[:space:]]*[^&]' ; then
    # Output redirection — check if destination matches inbox/archive
    # This regex catches `> inbox/foo`, `>> archive/bar`, etc.
    if echo "$command" | grep -qE '>+[[:space:]]*([^&;|]*/)?((inbox|archive)/)'; then
        block_reason="output redirection into inbox/ or archive/"
    else
        exit 0
    fi
elif echo "$command" | grep -qE '(^|[;&|` ])(cat|ls|find|grep|head|tail|less|more|wc|file|stat|diff|md5|sha256sum)[ \t]'; then
    # Explicit read-only command, allow
    exit 0
else
    # Unknown command referencing inbox/archive — err on the safe side
    block_reason="unrecognized command referencing inbox/ or archive/"
fi

cat <<EOF >&2
BLOCKED: $block_reason

inbox/ and archive/ are immutable. The only sanctioned ways to modify them:

  1. scripts/archive_inbox.py — moves processed inbox items to archive/inbox/
  2. scripts/migrate_v2_to_v3.py — moves deprecated files into inbox/
  3. User actions outside the agent (Obsidian UI, Finder, etc.)

Command that was blocked:
  $command

If you need to process an inbox item, use the ingest skill and let
archive_inbox.py move it after. Never use mv/rm/cp/redirection on
inbox or archive paths directly.
EOF
exit 2
