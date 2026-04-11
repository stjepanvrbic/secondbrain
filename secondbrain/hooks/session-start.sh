#!/usr/bin/env bash
# SessionStart hook — injects the secondbrain routing/discipline rules as a
# systemMessage so the agent has them before responding to the first user turn.
#
# The systemMessage below is the high-signal bootstrap. The verbose version
# lives in references/session-start-bootstrap.md and is loaded by the
# session-start skill itself. Keep this payload tight — roughly under 1.5k
# chars — so it stays cheap on every session start.
#
# NOTE: single-quoted heredoc so ${CLAUDE_PLUGIN_ROOT} is emitted literally
# for the agent to resolve at read time.

cat <<'EOF'
{
  "systemMessage": "Run /secondbrain:session-start as your first action, BEFORE responding to the user. This is MANDATORY at the start of every session and on every clear/compact.\n\nSKILL ROUTING: auto-invoke `ingest` on brain dumps (unstructured multi-fact input, pasted email/Slack, screenshots, voice transcripts, 'dump' signals); auto-invoke `knowledge-search` on questions about the user's own plans/people/decisions/timeline; auto-invoke `whats-next` on completion signals or 'what now?' questions; NEVER auto-invoke `dream-protocol` (scheduled task only).\n\nSTATE CHANGES: write to the vault IMMEDIATELY. Information that lives only in conversation is LOST. Do NOT batch for session-end.\n\nRE-INVOKE session-start if >30 minutes pass without a context refresh, or if the user references something you don't have context for.\n\nLoad @${CLAUDE_PLUGIN_ROOT}/references/session-start-bootstrap.md, @${CLAUDE_PLUGIN_ROOT}/references/ingestion-rules.md, and @${CLAUDE_PLUGIN_ROOT}/references/communication-rules.md at session start. User-specific context (bio, rhythms, preferences) lives in me/profile.md in the vault."
}
EOF
