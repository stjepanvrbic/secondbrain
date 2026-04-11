---
name: dream-protocol
description: >
  This skill should be used when a "scheduled nightly task" runs at approximately
  2am, when the user asks to "run dream protocol", "run nightly maintenance",
  or "run vault maintenance", or when invoked by init for first-time setup.
  Performs a reflective consolidation pass over the vault — orienting on current
  state, gathering recent signal, consolidating changes, and verifying integrity.
metadata:
  version: "3.3.4"
---

# Core Rule

Nightly reflective pass over the vault. Synthesize recent activity into durable, well-organized vault state so future sessions orient quickly. Runs from scheduled task (~2am) or invoked by init for first-time setup.

# Prerequisites

1. Read `_MANIFEST.md` for current vault state.
2. For vault navigation, read `@${CLAUDE_PLUGIN_ROOT}/references/vault-navigation.md`.
3. For content templates, read `@${CLAUDE_PLUGIN_ROOT}/references/templates.md`.
4. For shared write rules (wikilinks, metadata, atomic sections, entity stubs), read `@${CLAUDE_PLUGIN_ROOT}/references/ingestion-rules.md`.
5. For the named DQL queries used in Phase 2, read `@${CLAUDE_PLUGIN_ROOT}/references/dql-patterns.md`.
6. For script commands (`verify_vault.py`, `rebuild_manifest.py`, `archive_inbox.py`), read `@${CLAUDE_PLUGIN_ROOT}/references/script-invocations.md`.

# Phase 1 — Orient

- Read _MANIFEST.md to understand current vault state and last dream run
- Read brain/status.md for last session context
- Skim brain/session-log.md for recent sessions since last dream run
- List inbox/ for unprocessed files
- Understand what exists and what changed recently BEFORE touching anything

# Phase 2 — Gather Recent Signal

Use DQL queries to find what needs attention. Don't exhaustively read everything — query narrowly for what matters. Queries are defined once in `@${CLAUDE_PLUGIN_ROOT}/references/dql-patterns.md`; reference them by name here.

- **Unprocessed inbox files** — run the `unprocessed-inbox` query.
- **Session log entries since last dream run** — scan for:
  - Unprocessed TODOs and action items
  - Decisions made that should be in brain/decisions.md
  - New entities mentioned but not yet created
  - Context and insights worth persisting to vault files
  - Status changes not yet reflected in brain/status.md
  - Transcript search — if you need specific context (e.g., "what was the error message from yesterday's build failure?"), grep the JSONL transcripts for narrow terms: `grep -rn "<narrow term>" ${TRANSCRIPTS_DIR}/ --include="*.jsonl" | tail -50`. Don't exhaustively read transcripts — only look for things you already suspect matter.
- **Stale tasks** — run the `stale-tasks` query (no movement >14 days).
- **Tasks approaching deadlines** — run the `approaching-deadlines` query. Results not already in "Urgent This Week" are promoted in Phase 3.
- **Archive candidates** — run the `archive-candidates` query (completed tasks >7 days old).
- **Broken wikilinks and orphan files** — use `verify_vault.py` per `@${CLAUDE_PLUGIN_ROOT}/references/script-invocations.md`. DQL cannot traverse link targets.
- **Entities flagged for verification** — run the `entities-to-verify` query (finds `[verify:: true]` inline markers ingest left behind for ambiguous entity links). Phase 3 resolves or promotes each.
- **Contradicted content** — vault statements that no longer reflect current reality.

# Phase 3 — Consolidate

Process everything found in Phase 2. Full details in `references/execution-pipeline.md` (skill-local). Script invocations in `@${CLAUDE_PLUGIN_ROOT}/references/script-invocations.md`.

- Process inbox items: run `archive_inbox.py` after routing; never mark items processed by hand
- Route inbox items to vault via ingest routing rules
- Extract unprocessed signal from session log entries
- Promote tasks approaching deadlines to "Urgent This Week"
- Flag stale tasks (>14 days), move to Someday
- Fix broken wikilinks (fuzzy match, create missing entities via `create_entity_stubs.py`)
- Resolve `[verify:: true]` entity markers: for each, fuzzy-match against existing entity files (same mechanism used for broken wikilinks). If a clear canonical entity exists, update the wikilink to point at it, remove the `[verify:: true]` flag, and log the resolution. If no match, append the context (original file, line, surrounding sentence, timestamp) to `scratch/to-verify.md` for human review and leave the inline flag in place. Log either outcome in `log.md`.
- Deduplicate near-identical content (keep canonical, replace with wikilink)
- Add wikilinks to orphan content mentioning known entities
- Archive completed tasks >7 days old to monthly archive
- Refresh brain/status.md hot cache
- Check project completion by domain
- Convert relative dates to absolute dates
- Resolve contradicted content via **soft-archive** (never delete):
  - Move the superseded file (or the extracted superseded section as a new file) to `archive/contradictions/YYYY-MM/<slug>.md` via MCP `vault_create` + `vault_delete`.
  - Write a sidecar `<slug>.sidecar.md` next to it containing: (1) the superseded content verbatim, (2) the new content that contradicts it, (3) where the new content came from (session-log entry, inbox file, entity page, etc.), (4) the reasoning for choosing the new over the old.
  - Update the live vault file with the new content, linking back to the archived copy so the original is recoverable.
  - Append to `log.md`: `## [YYYY-MM-DD HH:MM] dream-protocol | contradiction-resolved | <subject>` with a one-line body pointing at both the live file and the archived copy.
  - No hard deletes. The archived content remains recoverable.

# Phase 4 — Verify & Index

Script invocations live in `@${CLAUDE_PLUGIN_ROOT}/references/script-invocations.md`.

- Run the full-scan `verify_vault.py --json` to get the issue report.
- Run `verify_vault.py --fix` for auto-fixable issues.
- Fix remaining issues, re-verify until clean (see `references/execution-pipeline.md` for issue-type handling).
- Run `rebuild_manifest.py` to regenerate `_MANIFEST.md`. The rebuild:
  - **Regenerates the Content Catalog** by walking all `.md` files outside `inbox/`, `archive/`, `scratch/`, extracting the first non-frontmatter heading and any leading `> ...` blockquote summary, grouped by parent folder (entities, projects, concepts, reference).
  - **Rebuilds the Recent Activity section** from the last 7 days of `log.md` entries.

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

- Modifying `me/profile.md` or any legacy plugin-generated `CLAUDE.md` at the vault root
- Deleting any files (move to archive, never delete)
- Running during normal sessions (unless invoked by init)
- Committing without descriptive message
- Exhaustively reading every file (use DQL queries)
