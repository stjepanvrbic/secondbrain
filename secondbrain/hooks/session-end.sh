#!/usr/bin/env bash
# SessionEnd hook — tells the agent to run the session-end skill.

cat <<'EOF'
{
  "systemMessage": "Run /secondbrain:session-end — this is MANDATORY before session closes. Flush all state to vault."
}
EOF
