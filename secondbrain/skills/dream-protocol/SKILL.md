---
name: dream-protocol
description: >
  This skill should be used when a "scheduled nightly task" runs at approximately
  2am, when the user asks to "run dream protocol", "run nightly maintenance",
  or "run vault maintenance", or when invoked by init for first-time setup.
  Performs a reflective consolidation pass over the vault — orienting on current
  state, gathering recent signal, consolidating changes, and verifying integrity.
metadata:
  version: "3.3.0"
---

# Core Rule

Nightly reflective pass over the vault. Synthesize recent activity into durable, well-organized vault state so future sessions orient quickly. Runs from scheduled task (~2am) or invoked by init for first-time setup.

# Prerequisites

1. Read `_MANIFEST.md` for current vault state.
2. For vault navigation, read `@${CLAUDE_PLUGIN_ROOT}/references/vault-navigation.md`.
3. For content templates, read `@${CLAUDE_PLUGIN_ROOT}/references/templates.md`.

# Phase 1 — Orient

- Read _MANIFEST.md to understand current vault state and last dream run
- Read brain/status.md for last session context
- Skim brain/session-log.md for recent sessions since last dream run
- List inbox/ for unprocessed files
- Understand what exists and what changed recently BEFORE touching anything

# Phase 2 — Gather Recent Signal

Use DQL queries to find what needs attention. Don't exhaustively read everything — query narrowly for what matters.

- Unprocessed inbox files (where processed != true)
- Session log entries since last dream run — scan for:
  - Unprocessed TODOs and action items
  - Decisions made that should be in brain/decisions.md
  - New entities mentioned but not yet created
  - Context and insights worth persisting to vault files
  - Status changes not yet reflected in brain/status.md
  - Transcript search — if you need specific context (e.g., "what was the error message from yesterday's build failure?"), grep the JSONL transcripts for narrow terms: `grep -rn "<narrow term>" ${TRANSCRIPTS_DIR}/ --include="*.jsonl" | tail -50`
    - Don't exhaustively read transcripts. Look only for things you already suspect matter.
- Stale tasks (no movement >14 days)
- Tasks approaching deadlines (due within 7 days, not yet in Urgent)
- Completed tasks >7 days old (archive candidates)
- Files with broken wikilinks or zero outgoing links
- Existing vault content that contradicts current state

# Phase 3 — Consolidate

Process everything found in Phase 2. Full details in `references/execution-pipeline.md`.

- Process inbox items: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/archive_inbox.py "${VAULT_PATH:-$HOME/vault}"` for inbox processing instead of marking items as processed manually
- Route inbox items to vault via ingest routing rules
- Extract unprocessed signal from session log entries
- Promote tasks approaching deadlines to "Urgent This Week"
- Flag stale tasks (>14 days), move to Someday
- Fix broken wikilinks (fuzzy match, create missing entities from templates)
- Deduplicate near-identical content (keep canonical, replace with wikilink)
- Add wikilinks to orphan content mentioning known entities
- Archive completed tasks >7 days old to monthly archive
- Refresh brain/status.md hot cache
- Check project completion by domain
- Convert relative dates to absolute dates
- Delete contradicted facts at the source — if new info disproves old, fix it

# Phase 4 — Verify & Index

- Run verify-vault.py: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify_vault.py "${VAULT_PATH:-$HOME/vault}" --json`
- For auto-fixable issues, run: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify_vault.py "${VAULT_PATH:-$HOME/vault}" --fix`
- Fix remaining issues found, re-verify until clean (see execution-pipeline.md for issue handling)
- Run manifest rebuild: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/rebuild_manifest.py "${VAULT_PATH:-$HOME/vault}"`
  - **Regenerate the Content Catalog section** by walking all `.md` files outside `inbox/`, `archive/`, `scratch/`, extracting the first non-frontmatter heading and any leading `> ...` blockquote summary, grouping by parent folder (entities, projects, concepts, reference)
  - **Rebuild the Recent Activity section** from the last 7 days of `log.md` entries

# Phase 5 — Append to log.md

After Phase 4 completes, append a single entry to `log.md` summarizing the run:

```markdown
## [YYYY-MM-DD HH:MM] dream-protocol | Run #N
Inbox: X processed. Session signal: X items extracted. Tasks: X archived, X promoted, X stale-flagged. Links: X fixed. Verification: pass/N issues. Manifest rebuilt.
```

This is append-only — never edit existing entries. The log.md format is documented in `@${CLAUDE_PLUGIN_ROOT}/references/vault-navigation.md`.

# Execution Timing

- Scheduled: ~2:00am local time, nightly
- Duration: ~5-15 minutes
- Condition: only run if vault is accessible

# Output

Brief summary of what was consolidated, updated, or pruned. If nothing changed, say so.

```
dream-protocol completed: [ISO timestamp]

Summary:
- Inbox: X files processed
- Session signal: X items extracted
- Tasks: X archived, X promoted, X stale-flagged
- Links: X fixed
- Verification: pass/N issues
- Manifest: rebuilt

Next session recommended focus: [[domain-name]]
```

# Error Handling

- Vault unreachable: log error, queue for next run
- Broken wikilink can't be fixed: mark "[BROKEN LINK: xxx]" for manual review
- verify-vault.py not found: skip verification, note in output
- Manifest missing: rebuild from scratch (this IS the cold start path)

# Forbidden Actions

- Modifying CLAUDE.md
- Deleting any files (move to archive, never delete)
- Running during normal sessions (unless invoked by init)
- Committing without descriptive message
- Exhaustively reading every file (use DQL queries)
