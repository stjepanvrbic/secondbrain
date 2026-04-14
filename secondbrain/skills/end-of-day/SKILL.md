---
name: end-of-day
description: >
  This skill should be used when the "end of day capture scheduled task" runs
  (~7:30pm), or when the user says "end of day", "wrap up", or "capture my day".
  Reviews the day's plan vs accomplishments, prompts for a brain dump, flushes
  state to vault, and commits. Scheduled task only.
metadata:
  version: "3.5.25"
---

# Core Rule

Capture end-of-day state and flush to vault. Review plan vs accomplishments, prompt for brain dump, update status, write session log, commit. Information left only in conversation is LOST.

# Prerequisites

1. Read `_MANIFEST.md` for current vault state.
2. For vault navigation, read `@${CLAUDE_PLUGIN_ROOT}/references/vault-navigation.md`.
3. For content templates, read `@${CLAUDE_PLUGIN_ROOT}/references/templates.md`.

# Steps

## 1. Review Today's Plan vs Accomplishments

Read brain/status.md — find "Today's Plan" section (written by morning-brief). Assess what was planned vs what was done. Note tasks completed but not yet marked done, and tasks planned but not started.

## 2. Prompt for Brain Dump

Match intensity to the day:
- **Heavy day** (many tasks, deadlines): "Big day — what happened with [specific items]? Anything else to capture?"
- **Light day** (few tasks): "Quick check-in — anything to capture from today?"

If user responds with a brain dump, process it via ingest routing:
- Tasks → brain/status.md
- Decisions → brain/decisions.md
- Entity info → entities/{name}.md
- Ideas → scratch/ideas.md

If user doesn't respond, continue — still write status and session log from vault state.

## 3. Carry Forward Incomplete Tasks

For tasks planned today but not completed:
- If they have [due::] of today, add `[OVERDUE]` flag
- Note carry-forwards in status.md

## 4. Update brain/status.md

Write "Last Session Summary" using template from `@${CLAUDE_PLUGIN_ROOT}/references/templates.md`. Keep brief — domains worked, key accomplishments, where left off, blockers, focus domain.

## 5. Write Session Log Entry

Prepend to brain/session-log.md using session log template. Bullet points only, past tense, all [[wikilinks]].

## 6. Check Inbox

Note any unprocessed inbox files in status.md "Inbox pending" section. Do NOT process now.

# Error Handling

- No "Today's Plan" found: skip plan vs accomplishments, capture what happened from vault state
- User doesn't respond to brain dump prompt: continue with vault-based capture
- Status.md or session-log.md missing: create from templates

# Forbidden Actions

- Letting the capture complete without writing to vault
- Leaving information only in conversation
- Deleting inbox files
