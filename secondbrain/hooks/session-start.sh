#!/usr/bin/env bash
# SessionStart hook — tells the agent to run the session-start skill.
# Uses command type (not prompt) because prompt hooks require ToolUseContext
# which isn't available at SessionStart.

cat <<'EOF'
{
  "systemMessage": "Run /secondbrain:session-start — this is MANDATORY at the start of every session."
}
EOF
