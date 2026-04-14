---
name: dream-protocol
description: >
  This skill should be used when a "scheduled nightly task" runs at approximately
  2am, when the user asks to "run dream protocol", "run nightly maintenance",
  or "run vault maintenance", or when invoked by init for first-time setup.
  Orchestrates a semantic worker followed by a structural worker so the vault
  ends the run in a fully healthy state.
metadata:
  version: "3.5.18"
---

# Core Rule

Nightly vault repair orchestrator. Run the two worker skills in sequence:

1. `@${CLAUDE_PLUGIN_ROOT}/skills/dream-protocol-semantic/SKILL.md`
2. `@${CLAUDE_PLUGIN_ROOT}/skills/dream-protocol-structural/SKILL.md`

This skill is complete only when the structural worker leaves the vault at the
healthy-vault target: final `verify_vault.py --json` returns `0 errors, 0 warnings`.

# Prerequisites

1. Read `_MANIFEST.md` for current vault state.
2. Read `@${CLAUDE_PLUGIN_ROOT}/references/healthy-vault.md`.
2. For vault navigation, read `@${CLAUDE_PLUGIN_ROOT}/references/vault-navigation.md`.
3. For content templates, read `@${CLAUDE_PLUGIN_ROOT}/references/templates.md`.
4. For shared write rules (wikilinks, metadata, atomic sections, entity stubs), read `@${CLAUDE_PLUGIN_ROOT}/references/ingestion-rules.md`.
5. For the named DQL queries used in Phase 2, read `@${CLAUDE_PLUGIN_ROOT}/references/dql-patterns.md`.
6. For script commands (`verify_vault.py`, `rebuild_manifest.py`, `archive_inbox.py`, `archive_contradiction.py`), read `@${CLAUDE_PLUGIN_ROOT}/references/script-invocations.md`.
7. Read the worker skill instructions at `@${CLAUDE_PLUGIN_ROOT}/skills/dream-protocol-semantic/SKILL.md` and `@${CLAUDE_PLUGIN_ROOT}/skills/dream-protocol-structural/SKILL.md`.

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
- **Stale tasks** — run the `stale-tasks` query (no movement >14 days).
- **Tasks approaching deadlines** — run the `approaching-deadlines` query. Results not already in "Urgent This Week" are promoted in Phase 3.
- **Archive candidates** — run the `archive-candidates` query (completed tasks >7 days old).
- **Broken wikilinks and orphan files** — use `verify_vault.py` per `@${CLAUDE_PLUGIN_ROOT}/references/script-invocations.md`. DQL cannot traverse link targets.
- **Entities flagged for verification** — run the `entities-to-verify` query (finds `[verify:: true]` inline markers ingest left behind for ambiguous entity links). Phase 3 resolves or promotes each.
- **Contradicted content** — vault statements that no longer reflect current reality.

# Phase 3 — Semantic Consolidation

Run the semantic worker responsibilities from
`@${CLAUDE_PLUGIN_ROOT}/skills/dream-protocol-semantic/SKILL.md`.
Full details remain in `references/execution-pipeline.md` (skill-local).
Script invocations live in `@${CLAUDE_PLUGIN_ROOT}/references/script-invocations.md`.

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
  - Move the superseded content to `archive/contradictions/YYYY-MM/<slug>.md` via `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/archive_contradiction.py` (see `@${CLAUDE_PLUGIN_ROOT}/references/script-invocations.md`). The script writes both the archive file and a sidecar `<slug>.sidecar.md` containing: (1) the superseded content verbatim, (2) the new content that contradicts it, (3) where the new content came from (session-log entry, inbox file, entity page, etc.), (4) the reasoning for choosing the new over the old.
  - MCP `vault_create` is hook-blocked for `archive/*` — always go through the script. The script handles directory creation, slug collision suffixes, and section extraction.
  - Then edit the live vault file in place to the new state, adding a one-line blockquote backlink to the archived copy (`> Archived at [[archive/contradictions/YYYY-MM/<slug>]]`). Never call `vault_delete` on the original — the script doesn't touch it, and neither should you.
  - Append to `log.md`: `## [YYYY-MM-DD HH:MM] dream-protocol | contradiction-resolved | <subject>` with a one-line body pointing at both the live file and the archived copy.
  - No hard deletes. The archived content remains recoverable.

# Phase 4 — Structural Maintenance

Run the structural worker responsibilities from
`@${CLAUDE_PLUGIN_ROOT}/skills/dream-protocol-structural/SKILL.md`.
Script invocations live in `@${CLAUDE_PLUGIN_ROOT}/references/script-invocations.md`.

- Run the full-scan `verify_vault.py --json` to get the issue report.
- Run `verify_vault.py --fix` for auto-fixable issues.
- Fix remaining issues, re-verify until clean.
- "Clean" means the final verifier returns `0 errors, 0 warnings`.
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

# Phase 6 — Commit nightly state

After Phase 5's log.md append, commit any uncommitted vault changes from the night's work as a single nightly checkpoint. This is the same `commit-stop` subcommand the Stop hook uses per turn, so the nightly commit is consistent with the per-turn commits in style and author identity.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/vault_git.py commit-stop \
    --vault "${VAULT_PATH}" \
    --message "dream-protocol nightly checkpoint"
```

**Git is opt-in.** If `vault_git.py` reports that the vault is not a git repo (exit non-zero with `not a git repo`), **skip silently** — the user opted out of git during init and the nightly run should not complain. Do not print an error, do not surface it to the user, and do not attempt to `vault_git.py init` on their behalf.

**Fail soft on real errors.** If `commit-stop` fails for any reason other than "not a git repo" (missing git binary, permission issue, file system weirdness, etc.), log the stderr verbatim to `log.md` as:

```markdown
## [YYYY-MM-DD HH:MM] dream-protocol | commit-stop failed
<captured stderr>
```

Then continue. dream-protocol should **never hard-fail** on a commit issue — the vault content is already on disk from Phases 1-5, so the night's work is durable regardless of whether the commit landed. Doctor or the next Stop hook invocation will retry the commit.

# Phase 7 — Regenerate hot memory

After Phase 6 (commit), regenerate `brain/hot-memory.md` from scratch
based on the now-clean vault state:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/update_hot_memory.py \
    --regenerate --vault "${VAULT_PATH}" \
    --desktop-config-path "${SECONDBRAIN_CLAUDE_DESKTOP_CONFIG:-$HOME/Library/Application Support/Claude/claude_desktop_config.json}"
```

The script reads vault state via Connect MCP, builds the hot-memory
structure per the T10 schema, validates the token budget, and writes
the file atomically. The PreToolUse `enforce-immutability.sh` hook
protects `brain/hot-memory.md` from agent writes; the script bypasses
that guard via a direct HTTP call to Connect MCP (same path the ingester
uses when it runs `update_hot_memory.py --apply`).

**Fail soft.** If the regenerate script exits non-zero, log the stderr
verbatim to `.secondbrain/ingest-log.md` and leave the existing
hot-memory file in place. Do not delete or truncate it. The next
ingest cycle will keep updating it incrementally via `--apply`, so a
single failed regenerate is a transient inconvenience, not data loss.

```markdown
## [YYYY-MM-DD HH:MM] dream-protocol | hot-memory regenerate failed
<captured stderr>
```

Phase 7 runs after the commit so the regenerated hot-memory reflects
the committed state on disk — if we regenerated before committing, the
hot-memory would snapshot a state the user could never `git log` back
to.

# Execution Timing

- Scheduled: ~2:00am local time, nightly
- Duration: ~5-15 minutes
- Condition: only run if vault is accessible

# Output

Brief summary of what was consolidated, updated, or pruned. If nothing changed, say so.
Do not claim success unless the final health target was reached.
Keep the scheduled-task response compact: counts, verification state, and at
most 1 next-focus hint.

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

# Auto-Invocation Policy

**Auto-invocation is FORBIDDEN.** The agent must never decide on its own to run this skill during a normal session. Allowed invocations are limited to:

- The nightly scheduled task at ~2am (primary path)
- `init` during Scenario 2 (connect existing vault) setup
- Explicit user request ("run dream protocol", "run vault maintenance", "run nightly maintenance", "consolidate")

Agent-initiated runs during a normal session are forbidden because dream-protocol mutates many files in bulk and would surprise the user. If you think the vault needs consolidation mid-session, surface the observation — do not act on it.

# Forbidden Actions

- Modifying `me/profile.md` or any legacy plugin-generated `CLAUDE.md` at the vault root
- Deleting any files (move to archive, never delete)
- Self-triggering during a normal session (see Auto-Invocation Policy above)
- Committing without descriptive message
- Exhaustively reading every file (use DQL queries)
