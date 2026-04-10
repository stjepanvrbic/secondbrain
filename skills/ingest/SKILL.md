---
name: ingest
description: >
  This skill should be used when the user sends a "brain dump", "random thoughts",
  unstructured text with multiple pieces of info, copy-pasted content from email
  or Slack, screenshots, voice transcripts, or when session-start detects
  unprocessed inbox files. Routes raw input to structured vault entries with
  mandatory wikilink enforcement.
metadata:
  version: "3.1.0"
---

# Core Rule

All raw input becomes structured vault entries with full wikilinks and metadata. NO information enters vault unlinked. One-line confirmation only — no narration.

# Prerequisites

1. Read `_MANIFEST.md` for current vault state.
2. For vault navigation, read `@${CLAUDE_PLUGIN_ROOT}/references/vault-navigation.md`.
3. For content templates, read `@${CLAUDE_PLUGIN_ROOT}/references/templates.md`.

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

## Rule 1: Wikilink Enforcement (NON-NEGOTIABLE)

Every piece of ingested content MUST be linked to ALL related entities.

For each piece of info, ask:
- Who is involved? → `[[entities/person-name]]`
- What domain? → `[[domain-name]]`
- What decision/goal does this relate to? → `[[brain/decisions#section-name]]`
- What other sections does this cross-reference? → `[[file#section]]`

**NO UNLINKED INFORMATION ENTERS THE VAULT.** If text is written without wikilinks, go back and add them.

## Rule 2: Inline Metadata (for tasks)

Every task gets full metadata inline:

```markdown
- [ ] Task description [[entity]] #domain [due:: 2026-MM-DD] [energy:: low|medium|high] [est:: 15min|30min|1hr|2hr]
```

Field order: entity link → #domain → [due::] → [energy::] → [est::]

## Rule 3: Atomic Sections

Structure writes as atomic sections with clear headings, 1-3 bullets, wikilinks throughout.

## Rule 4: Entity Creation

If ingestion mentions a new entity not in the vault `entities/` folder, create the entity file. See **`references/routing-rules.md`** for template.

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
After ALL writes are complete:
1. Run: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify_vault.py ${VAULT_PATH} --modified-only [files-you-touched] --json`
2. If errors found (missing entities, broken links), fix immediately
3. For missing entities: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/create_entity_stubs.py ${VAULT_PATH} entity-name`
4. Do NOT mark the operation as complete until validation passes

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
- **Ambiguous entity names**: Create or link to best-guess entity, note "verify entity link"
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
- Entity names: kebab-case in filenames, wikilink as full name `[[entities/Xavier Laurens]]`
- Metadata order on tasks: entity → #domain → [due::] → [energy::] → [est::]
- If input has >10 items, process all, confirmation stays one line
