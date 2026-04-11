---
name: session-end
description: >
  This skill should be used when the user signals "done", "bye", "goodnight",
  "that's it", "done for now", when a SessionEnd hook fires, or when the user
  asks to "end the session" or "save and close". Flushes all session state to
  vault files, and appends session log. MANDATORY last action of
  every session.
metadata:
  version: "3.3.1"
---

# Core Rule

**MANDATORY, LAST ACTION OF EVERY SESSION.** When the user signals done, invoke this skill BEFORE session closes. Information that exists only in conversation is LOST information.

# Prerequisites
1. Read `_MANIFEST.md` for current vault state.
2. For vault navigation, read `@${CLAUDE_PLUGIN_ROOT}/references/vault-navigation.md`.
3. For content templates, read `@${CLAUDE_PLUGIN_ROOT}/references/templates.md`.

# Pre-Conditions (verify before executing)
- Read current brain/status.md BEFORE writing to detect existing sections
- Before appending "Last Session Summary", check if one already exists for today. If so, REPLACE it, don't append a duplicate.

# Execution Steps (in order)

## 1. Update brain/status.md

Append a new "Last Session Summary" section:

```markdown
## Last Session Summary — [ISO timestamp]

**Date:** YYYY-MM-DD
**Duration:** ~X minutes
**Domain(s) worked on:** [[domain-name]]
**Key accomplishments:**
- Item 1
- Item 2

**Where I left off:**
[One or two sentences — exactly what was in progress and the next step]

**New blockers discovered:**
- Blocker 1: [[related-entity]]

**Current focus domain:** [[domain-name]]
**Status:** on-track | needs-attention | blocked
```

Keep brief (3-5 lines max). Cite entities with [[wikilinks]]. State exactly where work paused.

## 2. Flush Pending Vault Changes

For ANY state changes made during session (new tasks, decisions, contacts, deadlines, status updates):
- Write immediately to appropriate vault file(s)
- Ensure all wikilinks are in place
- This step confirms all changes are flushed (error check)

## 3. Append to brain/session-log.md

Add entry at the top of file (reverse chronological):

```markdown
## Session — [ISO timestamp]

**Duration:** ~X minutes
**Domains:** [[domain1]], [[domain2]]
**Work done:**
- Specific accomplishment 1

**Decisions made:**
- Decision 1: [[entity]]

**Blockers identified:**
- Blocker: [[entity]]

**Next session focus:** [[domain-name]]
```

Keep to 1-3 bullet points per section. Always cite with [[wikilinks]]. No narrative — bullet points only.

## 4. Check inbox/ for unprocessed files

```
FOR each file in inbox/:
  FLAG: "Unprocessed inbox item for next session: [filename]"
  ADD to brain/status.md "Inbox pending" section
ENDFOR
```

Do NOT process files now. Simply note them so next session starts aware. FORBIDDEN: Modifying or deleting inbox files.

## 5. Append to log.md (Karpathy-style chronological log)

Append a single entry to the vault root `log.md`:

```markdown
## [YYYY-MM-DD HH:MM] session-end | <one-line topic>
<one-to-three line body summarizing what shifted in the vault: tasks closed, decisions added, blockers flagged, key accomplishments>
```

This is **append-only** — never edit existing entries. Format details in `@${CLAUDE_PLUGIN_ROOT}/references/vault-navigation.md`.

The richer session details still go in `brain/session-log.md` (step 3 above). `log.md` is the lightweight greppable index across ALL operations, not just sessions.

# Post-Write Validation
After ALL writes are complete:
1. Run: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify_vault.py ${VAULT_PATH} --modified-only [files-you-touched] --json`
2. If errors found (missing entities, broken links), fix immediately
3. For missing entities: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/create_entity_stubs.py ${VAULT_PATH} entity-name`
4. Do NOT mark the operation as complete until validation passes

# Error Handling

- vault_patch fails -> read current file state, re-plan the edit, retry once
- Entity doesn't exist -> create stub via scripts/create_entity_stubs.py
- DQL query returns empty -> fall back to vault_search, then vault_list
- Never mark operation complete if validation failed
- If brain/status.md missing: create minimal with session summary
- If brain/session-log.md missing: create new
- If wikilinks broken: add them anyway
- If vault unreachable: respond "vault inaccessible"

# Forbidden Actions

- Letting a session close without running this skill
- Leaving information only in conversation
- Deleting inbox files
- Committing partial changes
- Modifying CLAUDE.md

# Implementation Notes

- ISO timestamp format: `2026-03-23T14:30:00` (local time, no timezone)
- "~X minutes" = estimate (do not calculate precisely)
- Domain names: use [[domain]] wikilink format
- All bullet points MUST include at least one [[wikilink]]
- If session was short (<5 min), summarize in one section: "Brief check-in: [what happened]"
