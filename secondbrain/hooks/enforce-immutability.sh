#!/usr/bin/env bash
# PreToolUse hook — enforces immutability of inbox/ and archive/.
#
# Blocks any MCP vault write operation targeting a path inside:
#   inbox/   — raw input, write-once by user or ingest
#   archive/ — immutable history, only managed by plugin scripts
#
# Python scripts in secondbrain/scripts/ use direct filesystem calls and
# are NOT affected by this hook. That's intentional — archive_inbox.py and
# migrate_v2_to_v3.py are the only sanctioned way to modify these directories.
#
# Exit 0  → allow the operation
# Exit 2  → block (Claude Code shows the stderr message to the agent)

set -euo pipefail

# Read the tool_input JSON from stdin
input=$(cat)

# Extract path from tool_input.path using python (portable, no jq dep)
path=$(python3 -c "
import sys, json
try:
    data = json.loads(sys.stdin.read())
    tool_input = data.get('tool_input', {})
    print(tool_input.get('path', ''))
except Exception:
    print('')
" <<< "$input")

if [ -z "$path" ]; then
    exit 0  # no path — can't check, allow
fi

# Normalize: strip leading slash
normalized="${path#/}"

# Check for immutable prefixes
case "$normalized" in
    inbox/*|inbox)
        cat <<'EOF' >&2
BLOCKED: inbox/ is immutable via MCP.

Raw input in inbox/ should never be modified by the agent. It can only
be:
  1. Added by the user (via Obsidian, Finder, brain dump → ingest, etc.)
  2. Moved to archive/inbox/ via scripts/archive_inbox.py after processing

If you need to process an inbox item, use the ingest skill and let
archive_inbox.py move it after.
EOF
        exit 2
        ;;
    archive/*|archive)
        cat <<'EOF' >&2
BLOCKED: archive/ is immutable.

The archive holds historical raw data and must never be modified by the
agent. It can only be appended to by these sanctioned scripts:
  - scripts/archive_inbox.py (moves processed inbox items in)
  - scripts/migrate_v2_to_v3.py (moves deprecated files to inbox for re-ingestion)

Never write, patch, edit, or delete anything in archive/ via MCP tools.
EOF
        exit 2
        ;;
    brain/hot-memory.md)
        cat <<'EOF' >&2
BLOCKED: brain/hot-memory.md is maintained exclusively by update_hot_memory.py.

Hot memory is a derived artifact — a compact fact index the ingester
rebuilds after each session. Direct MCP writes would overwrite the
ingester's work on the next run and break session-start context loading.

If you need to refresh hot-memory, run:
  python3 scripts/update_hot_memory.py <vault>

Never write, patch, edit, or delete brain/hot-memory.md via MCP tools.
EOF
        exit 2
        ;;
esac

exit 0
