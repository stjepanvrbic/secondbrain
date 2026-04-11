---
name: ingest
description: >
  This skill should be used when the user sends a "brain dump", "random thoughts",
  unstructured text with multiple pieces of info, copy-pasted content from email
  or Slack, screenshots, voice transcripts, or when session-start detects
  unprocessed inbox files. Routes raw input to structured vault entries with
  mandatory wikilink enforcement.
metadata:
  version: "3.3.4"
---

# Core Rule

All raw input becomes structured vault entries with full wikilinks and metadata. NO information enters vault unlinked. One-line confirmation only — no narration.

# Prerequisites

1. Read `_MANIFEST.md` for current vault state.
2. For vault navigation, read `@${CLAUDE_PLUGIN_ROOT}/references/vault-navigation.md`.
3. For content templates, read `@${CLAUDE_PLUGIN_ROOT}/references/templates.md`.
4. For shared write rules (wikilinks, metadata order, atomic sections, entity stubs), read `@${CLAUDE_PLUGIN_ROOT}/references/ingestion-rules.md`.
5. For script commands (`verify_vault.py`, `archive_inbox.py`, `create_entity_stubs.py`), read `@${CLAUDE_PLUGIN_ROOT}/references/script-invocations.md`.

# Pre-Conditions (verify before executing)
- MCP connection is live: `mcp__obsidian__vault_list` with path `/` returns
- `brain/status.md` exists

# Auto-Triggers

**MUST invoke when ANY of:**
- User sends unstructured text with multiple pieces of info
- User says "brain dump", "dump", "let me get this out", "random thoughts", "just throwing this at you"
- User pastes text from email, Slack, meeting notes, conversation, articles
- User sends screenshot (extract text from image) or voice transcript
- Session-start detects unprocessed files in the vault `inbox/` folder
- Stream-of-consciousness text that is NOT a direct question or task request

**FORBIDDEN: Responding to brain dump without running ingest first.**

# Processing Rules (MANDATORY)

All shared write rules — wikilink enforcement, inline metadata field order, atomic sections, and entity-stub creation — are in `@${CLAUDE_PLUGIN_ROOT}/references/ingestion-rules.md`. Load that file. Those rules apply to every ingest without exception.

Ingest-specific extensions on top of the shared rules:

- Routing table (which content type goes to which destination file) lives in `references/routing-rules.md` next to this skill.
- Every ingest ends with the Karpathy wiki pattern in "Processing Algorithm" below — a single ingest touches multiple pages, not just one destination.

# Processing Algorithm (Karpathy multi-page-touch discipline)

A single ingest should NOT just dump content into one file. It should spread the update across all relevant pages — primary destination, referenced entities, log, manifest. This is the "wiki maintenance" discipline.

```
FOR each logical unit in input (task, idea, decision, person, deadline):
  1. Determine type (task? idea? decision? entity? term?)
  2. Route to appropriate destination (see references/routing-rules.md)
  3. Extract ALL entities mentioned
  4. Ensure EVERY entity gets a [[wikilink]]
  5. Add inline metadata if task
  6. Write to destination file as atomic section (append, don't overwrite)
  7. VERIFY all wikilinks added
  8. Update referenced entity pages — for each [[entity]] linked from this
     content, append a one-line context note to the entity's file
     (e.g., "Mentioned in status {{date}} re: <topic>")
ENDFOR

# Spread the update (per Karpathy wiki pattern)
9. APPEND a single entry to log.md summarizing this ingest:
   ## [YYYY-MM-DD HH:MM] ingest | <one-line title>
   <body: counts of what was added, key entities updated>
   (See @${CLAUDE_PLUGIN_ROOT}/references/vault-navigation.md for log.md format)

10. The _MANIFEST.md content catalog is regenerated nightly by dream-protocol —
    no need to update it inline. New entities will appear in the catalog after
    the next dream run.

11. After processing inbox items, run:
    `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/archive_inbox.py ${VAULT_PATH}`
    to move processed files to archive/inbox/.

OUTPUT:
  "Got it — added [X tasks], [X ideas], [X decisions], created [X entities], updated [[entity1]] [[entity2]]."
  ONE LINE only.
```

# Post-Write Validation

Run the standard post-write validation block from
`@${CLAUDE_PLUGIN_ROOT}/references/script-invocations.md` after ALL writes
are complete. If errors are found (missing entities, broken links), fix
immediately. Do NOT mark the operation complete until validation passes.

# Response Style

**FORBIDDEN:**
- "Let me process this step by step..."
- Asking clarifying questions: "Did you mean...?"
- Summarizing what was extracted

**REQUIRED:**
```
Got it — added 2 tasks, 1 decision, updated [[entities/xavier-laurens]].
```

If ambiguity exists, make best guess and add note:
```
Got it — 1 task (unclear on deadline, guessed this week), 1 idea.
```

# Error Handling

- vault_patch fails -> read current file state, re-plan the edit, retry once
- Entity doesn't exist -> create stub via scripts/create_entity_stubs.py
- DQL query returns empty -> fall back to vault_search, then vault_list
- Never mark operation complete if validation failed
- **Ambiguous entity names**: Create or link to the best-guess entity AND append `[verify:: true]` inline on the same line as the wikilink (see Rule 2a in `@${CLAUDE_PLUGIN_ROOT}/references/ingestion-rules.md`). Dream-protocol queries these nightly and either (a) auto-resolves via fuzzy match once a canonical entity exists, or (b) promotes the context to `scratch/to-verify.md` for human review. NEVER leave a prose-only "verify entity link" comment — it must be a structured, DQL-queryable inline field.
- **Missing deadline**: Ingest without [due::], note in status.md
- **Unknown domain**: Create note in scratch/ideas.md flagged for review
- **Broken entity links**: Add them anyway, dream protocol fixes nightly
- **Very long brain dump**: Process in chunks, maintain logical grouping

# Forbidden Actions

- Creating new task files (brain/status.md is ONLY task file)
- Asking clarifying questions during dump
- Narrating processing step-by-step
- Modifying inbox files in place (they are moved to archive/inbox/ after processing)
- Leaving ANY text unlinked
- Using TASKS.md (does not exist in this system)

# Implementation Notes

- Timestamp format: ISO 8601 local time
- Entity names: kebab-case in filenames, wikilink as full name `[[entities/kebab-name|Display Name]]`
- Shared write rules (including task metadata order) are in `@${CLAUDE_PLUGIN_ROOT}/references/ingestion-rules.md`
- If input has >10 items, process all, confirmation stays one line
