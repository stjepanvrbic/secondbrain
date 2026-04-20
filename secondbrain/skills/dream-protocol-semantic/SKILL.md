---
name: dream-protocol-semantic
description: >
  Internal worker for /secondbrain:dream-protocol. Owns semantic vault
  consolidation only: reconcile status, process fresh signal, archive
  completed tasks, and resolve contradictions in live state before
  structural verification begins.
metadata:
  version: "3.6.2"
---

# Core Rule

This worker owns semantic/content maintenance only. It does not define health
and it does not declare completion. It prepares the vault so the structural
worker can verify and finish the run cleanly.

# Prerequisites

1. Read `@${CLAUDE_PLUGIN_ROOT}/references/healthy-vault.md`.
2. Read `@${CLAUDE_PLUGIN_ROOT}/references/vault-navigation.md`.
3. Read `@${CLAUDE_PLUGIN_ROOT}/references/templates.md`.
4. Read `@${CLAUDE_PLUGIN_ROOT}/references/ingestion-rules.md`.
5. Read `@${CLAUDE_PLUGIN_ROOT}/references/dql-patterns.md`.
6. Read `@${CLAUDE_PLUGIN_ROOT}/skills/dream-protocol/references/execution-pipeline.md`.

# Responsibilities

- auto-heal severe `log.md` bloat before any log read (Phase 0 of execution pipeline)
- gather recent signal from inbox, session log, status, and deadlines
- reconcile contradictory live state
- promote/demote/archive tasks based on current reality
- resolve stale semantic blockers
- route new entities, decisions, and deadlines
- convert relative dates to absolute dates

# Output

Leave the live vault semantically coherent for the structural worker.
Do not claim the vault is healthy. Hand off to `dream-protocol-structural`.
