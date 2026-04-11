# Dream Protocol Execution Pipeline

> Detailed procedures for each phase of the nightly vault maintenance run.
> Named DQL queries referenced below are defined once in
> `@${CLAUDE_PLUGIN_ROOT}/references/dql-patterns.md`.

---

## Phase 1 — Orient

Read vault state before touching anything. Understand what exists and what changed since the last dream run.

### 1.1 Read _MANIFEST.md

- Parse last dream run timestamp
- Note active domains, file counts, and vault health from previous run
- Identify any issues flagged for manual review

### 1.2 Read brain/status.md

- Identify last session focus and context
- Note any blockers or urgent items carried forward
- Check for overnight activity flags

### 1.3 Skim brain/session-log.md

- Read entries since the last dream run timestamp (do NOT read the entire history)
- Note which sessions occurred and their domains
- Flag entries that will need deeper scanning in Phase 2

### 1.4 List inbox/

- Count unprocessed files (where processed != true)
- Note file types and rough content categories

---

## Phase 2 — Gather Recent Signal

Run the named DQL queries from `@${CLAUDE_PLUGIN_ROOT}/references/dql-patterns.md`. Don't exhaustively read everything — query narrowly for what matters.

### 2.1 Unprocessed Inbox Files

Run the `unprocessed-inbox` query. Flag each result for routing in Phase 3.

### 2.2 Session Log Signal Extraction

Read `brain/session-log.md` entries since the last dream run timestamp (not the entire history) and scan each for:

- **Unprocessed TODOs and action items** — tasks mentioned but not yet in brain/status.md
- **Decisions made** — choices, conclusions, or direction changes that should be in brain/decisions.md
- **New entities mentioned** — people, companies, tools, concepts referenced but without an entities/ file
- **Context and insights** — observations, learnings, or status updates worth persisting to the relevant vault file
- **Status changes** — focus shifts, priority changes, or blockers not yet reflected in brain/status.md

Collect all extracted signal into a working list for Phase 3 processing.

### 2.3 Stale Tasks

Run the `stale-tasks` query. Cross-reference each result with brain/status.md for recent activity mentions — a task may appear stale by file modification but was discussed recently.

### 2.4 Deadline Proximity

Run the `approaching-deadlines` query. Check whether each result is already in the "Urgent This Week" section. Flag those that are not for promotion in Phase 3.

### 2.5 Archive Candidates

Run the `archive-candidates` query. Results move to `archive/completed-tasks-YYYY-MM.md` in Phase 3.8.

### 2.6 Broken Links & Orphans

DQL cannot traverse link targets — use `verify_vault.py --json` per `@${CLAUDE_PLUGIN_ROOT}/references/script-invocations.md`. Parse the JSON for `broken-wikilink` and `orphan` entries.

### 2.7 Entities Flagged for Verification

Run the `entities-to-verify` query from `@${CLAUDE_PLUGIN_ROOT}/references/dql-patterns.md`. Ingest leaves `[verify:: true]` inline whenever it had to guess an entity link. The query uses `FLATTEN file.lists AS L` so each result row is a single flagged bullet, not a whole file — you get `file.link` (source file) plus `L.text` (the bullet's literal text) for each hit. Collect the per-bullet rows for Phase 3.5a resolution.

### 2.8 Contradicted Content

Scan for vault content that contradicts current state:
- Status entries that no longer reflect reality (e.g., "blocked by X" when X is resolved)
- Task descriptions with outdated context
- Entity files with stale information

Do NOT resolve contradictions here — only gather. Phase 3.12 soft-archives them.

---

## Phase 3 — Consolidate

Process everything gathered in Phase 2. Each procedure below corresponds to signal identified during gathering.

### 3.1 Inbox Processing

```
FOR each unprocessed file in inbox/:
  1. Read file contents
  2. Route content using ingest routing rules:
     - Tasks       → brain/status.md
     - Ideas       → scratch/ideas.md
     - Decisions   → brain/decisions.md
     - Entities    → entities/{name}.md (use template from @${CLAUDE_PLUGIN_ROOT}/references/templates.md)
     - Glossary    → glossary.md
     - Status      → brain/status.md
     - Deadlines   → brain/deadlines.md
  3. Ensure ALL content has [[wikilinks]]
  4. ADD to file frontmatter:
     processed: true
     processed-date: [ISO timestamp]
     source: inbox-sweep
  5. Save file (NEVER delete)
ENDFOR
```

### 3.2 Session Signal Processing

```
FOR each signal item extracted in Phase 2.2:
  - TODOs/action items → route to brain/status.md (appropriate section)
  - Decisions → append to brain/decisions.md with date and context
  - New entities → create entity file from template (@${CLAUDE_PLUGIN_ROOT}/references/templates.md)
  - Context/insights → append to the relevant vault file (domain index, entity, or status)
  - Status changes → update brain/status.md accordingly

FOR each processed session entry:
  Flag in session-log.md: "[auto-processed by dream protocol]"
ENDFOR
```

### 3.3 Deadline Auto-Promotion

```
FOR each task flagged in Phase 2.4:
  IF task is in "This Week" or "Someday" section:
    1. MOVE task to "Urgent This Week" section
    2. ADD note: "[auto-promoted by deadline proximity]"
    3. LOG in brain/status.md "Urgent" section
ENDFOR
```

### 3.4 Stale Task Cleanup

```
FOR each task flagged in Phase 2.3:
  IF task has NO [done:: DATE] AND
     last update >14 days ago AND
     no recent activity in status.md or session-log.md:
    1. Flag task: "[STALE: No movement >14 days]"
    2. Move to "Someday" section with note
ENDFOR
```

### 3.5 Wikilink Repair

```
FOR each broken link flagged in Phase 2.6:
  1. Try to fix: search vault for likely match (fuzzy, >80% string similarity)
  2. IF match found → update link (e.g., [[Acme Corp]] → [[entities/acme-corp|Acme Corp]])
  3. IF no match found → mark "[BROKEN LINK: xxx]" for manual review
  4. IF link points to entity that doesn't exist:
     a. Create minimal entity file from template (@${CLAUDE_PLUGIN_ROOT}/references/templates.md)
     b. Note for manual review
ENDFOR
```

### 3.5a Verify-Entity Resolution

Ingest leaves `[verify:: true]` inline next to wikilinks it had to guess. Each Phase 2.7 query row is a single flagged **bullet** (from `FLATTEN file.lists AS L`), not a whole file — `file.link` is the source file and `L.text` is the bullet's literal text. Process each row:

```
FOR each [verify:: true] bullet row from Phase 2.7:
  1. Parse the wikilink out of L.text (e.g., [[entities/jane-smith|Jane]]).
  2. Extract the target stem from the wikilink (e.g., `jane-smith` from
     `[[entities/jane-smith|Jane]]`) — this is what you fuzzy-match against
     filenames in entities/.
  3. Fuzzy-match against existing entities/ files:
     A clear canonical entity exists when a SINGLE candidate matches with
     >80% similarity AND the gap to the next-best candidate is >20 percentage
     points. Otherwise, treat as deferred. (Same mechanism as Phase 3.5,
     section 3.5's wikilink repair.)
  4. IF a clear canonical entity exists:
     a. Rewrite the bullet in the source file to point at the canonical wikilink
     b. Remove the `[verify:: true]` marker from that bullet
     c. Append to log.md:
        ## [YYYY-MM-DD HH:MM] dream-protocol | verify-resolved | <entity>
        Linked <source-file> → [[entities/canonical]]
  5. IF no clear match:
     a. If scratch/to-verify.md does not exist, create it (dream-protocol
        creates it on first verify-deferred entry).
     b. Append a block to scratch/to-verify.md:
        ## <entity-guess> — [YYYY-MM-DD HH:MM]
        - Source: [[<source-file>]]
        - Context: "<L.text>"
        - Current wikilink: [[entities/best-guess|Best Guess]]
     c. Leave the `[verify:: true]` inline flag in place so the next dream run re-checks it
     d. Append to log.md:
        ## [YYYY-MM-DD HH:MM] dream-protocol | verify-deferred | <entity>
        Context moved to [[scratch/to-verify]]
ENDFOR
```

The flag is DQL-queryable and auto-heals when the canonical entity eventually exists, so the only manual work left is reviewing `scratch/to-verify.md`.

### 3.6 Deduplication

```
FOR each file in **/*.md:
  1. Extract all facts (sentences, statements)
  2. Compare against other files for duplicates (>90% sentence-level similarity)
  3. IF near-duplicate found:
     a. Keep canonical version (oldest source OR most detailed)
     b. Replace duplicates with [[wikilink]] to canonical
     c. Note in duplicate: "See [[canonical-source]]"
     d. LOG change: what was consolidated
ENDFOR
```

### 3.7 Orphan Wikilink Addition

```
FOR each orphan file flagged in Phase 2.6:
  FOR each paragraph/sentence without [[wikilinks]]:
    1. Identify nouns/entities mentioned
    2. IF entity exists in entities/ or glossary.md → ADD [[wikilink]]
    3. IF paragraph references specific section → ADD [[file#section]] reference
    4. LOG additions: file and count
  ENDFOR
ENDFOR
```

### 3.8 Done Task Archiving

```
FOR each task flagged in Phase 2.5:
  1. MOVE task to archive/completed-tasks-YYYY-MM.md (monthly archive)
  2. Keep [done::] timestamp in archive
  3. Remove from active status.md
ENDFOR
```

### 3.9 Hot Cache Update (brain/status.md)

```
REFRESH brain/status.md:
  1. Clear stale "Last Ingest" section
  2. Update "Current Focus" based on last session log
  3. Extract "Urgent This Week" count from status.md
  4. Extract blockers from status.md [blocked-by::] fields
  5. List files modified since last dream run (vault activity)
  6. Note stale tasks flagged in 3.4
  7. Note deadline promotions from 3.3
  8. Note session signal extracted in 3.2

Format:
  ---
  ## Vault State — [ISO timestamp]

  **Last session focus:** [[domain-name]]
  **Urgent this week:** X tasks
  **Blockers active:** [list]
  **Activity overnight:**
    - Inbox processed: X items
    - Session signal extracted: X items
    - Tasks promoted to urgent: X
    - Stale flags: X

  **Next session recommended focus:** [[domain-name]]
```

### 3.10 Project Completion Check

```
FOR each domain folder ({domain}):
  1. READ {domain}/index.md
  2. IF type == "project":
     a. Count tasks in brain/status.md tagged #domain
     b. Count DONE tasks from that #domain
     c. IF all tasks done (or all moved to archive):
        - FLAG: "[[domain-name]] ready for archiving?"
        - Move domain folder to archive/{domain}-YYYY-MM-DD/
        - Note for manifest update
ENDFOR
```

### 3.11 Date Normalization

```
FOR each file modified during this run:
  - Convert relative dates ("next Monday", "in 3 days") to absolute ISO dates
  - Ensure all [due::] fields use YYYY-MM-DD format
ENDFOR
```

### 3.12 Contradiction Soft-Archive

Never hard-delete contradicted content. Each contradiction goes into
`archive/contradictions/YYYY-MM/` via `archive_contradiction.py`, which
writes both the superseded-content file and a sidecar and is the only
sanctioned way to write under `archive/*` (MCP `vault_create` is blocked
there by the immutability hook).

Iterate **per contradiction**, not per file — one file can have multiple
independent contradictions in a single dream run, and each gets its own
archive + sidecar pair. Pattern matches Phase 3.8's per-item iteration.

```
FOR each contradicted item flagged in Phase 2.8:
  1. Derive a short subject for the archive filename (e.g., "acme-renewal-date").
     The script slugs it — no need to pre-slugify.

  2. Write the new content to a temp file the script can read:
        /tmp/sb-contradiction-<N>.md

  3. Determine scope — "smallest coherent unit":
     - If the whole file is obsolete, pass --original-file only.
     - If only a section is obsolete, pass --section-anchor "<Heading Text>"
       and the script extracts just that section (case-insensitive match,
       stopping at the next heading of the same or higher level).

  4. Invoke the script:
        python3 ${CLAUDE_PLUGIN_ROOT}/scripts/archive_contradiction.py \
          ${VAULT_PATH} \
          --original-file <path-to-original> \
          [--section-anchor "<Heading>"] \
          --new-content-file /tmp/sb-contradiction-<N>.md \
          --source-description "<where the new info came from, [[wikilink]] when possible>" \
          --reasoning "<why the new wins>" \
          --subject "<short subject>"
     The script handles directory creation, slug collisions (appends -1, -2,
     ...), and writes the sidecar with all FOUR required pieces of info:
        (1) Superseded content — verbatim
        (2) New content — the statement that now supersedes it
        (3) Source of new content — session-log entry, inbox file, entity
            page, email, etc. as a [[wikilink]] when possible
        (4) Resolution reasoning — why the new content wins (date, authority,
            direct observation, etc.)
     It prints a single JSON line to stdout with archive_path / sidecar_path /
     slug. Capture the paths.

  5. Edit the live vault file in place to the new state. Add a blockquote
     backlink pointing at the archived copy:
        > Archived at [[archive/contradictions/YYYY-MM/<slug>]]
     The script does NOT touch the original file — editing is the caller's job.

  6. Append to log.md:
        ## [YYYY-MM-DD HH:MM] dream-protocol | contradiction-resolved | <subject>
        <one-line body: live file + archive path>

  7. Clean up the temp file.
ENDFOR
```

The script never calls `vault_delete` and never edits the original — the
archive copy IS the preservation, and the live file is edited in place.
Recoverability is the point: any resolution can be inspected and reversed
by reading the archive + sidecar pair.

---

## Phase 4 — Verify & Index

Verification runs BEFORE manifest rebuild so the manifest reflects the verified state of the vault.

### 4.1 Vault Verification (Gate)

Dream protocol is NOT complete until this passes.

Run the verification script:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify_vault.py "${VAULT_PATH:-$HOME/vault}" --json
```

Parse the JSON output. For each issue found:

| Issue Type | Action |
|------------|--------|
| `broken-wikilink` with suggestion | Fix the link (e.g., `[[Acme Corp]]` → `[[entities/acme-corp\|Acme Corp]]`) |
| `broken-wikilink` without suggestion | Mark `[BROKEN LINK: target]` for manual review |
| `metadata` (task with no metadata) | Add missing [energy::] and [est::] fields based on task type |
| `conflict` (Syncthing duplicates) | Log to brain/status.md for manual review — do NOT auto-merge |
| `orphan` in inbox/ | Unprocessed inbox files — should have been handled in Phase 3.1. Flag in status.md |
| `orphan` outside inbox/ | Add a [[wikilink]] from the most relevant file, or note for review |
| `structure` | Critical — attempt to create missing dirs/files from templates (@${CLAUDE_PLUGIN_ROOT}/references/templates.md) |

After fixing issues, **run the script again**. Keep fixing and re-verifying until the script passes cleanly. The only issues left unfixed are ones that genuinely require manual intervention (e.g., Syncthing conflicts that need human decision on which version to keep). Log any such exceptions to brain/status.md with an explanation of why they couldn't be auto-fixed.

### 4.2 _MANIFEST.md Rebuild

Rebuild the manifest AFTER verification so it reflects the clean vault state.

```
READ  directory structure
REBUILD _MANIFEST.md with the following structure:
```

**Manifest Structure:**

```markdown
# Vault Manifest

> Auto-generated by dream-protocol. Last run: [ISO timestamp]

## Vault Health

| Metric              | Value   |
|---------------------|---------|
| Total files         | N       |
| Broken links        | N       |
| Orphan files        | N       |
| Stale tasks         | N       |
| Verification status | pass/fail |

## Active Domains

| Domain          | Type    | Files | Open Tasks | Status   |
|-----------------|---------|-------|------------|----------|
| [[domain-name]] | project | N     | N          | active   |
| ...             | ...     | ...   | ...        | ...      |

## File Tree

- brain/
  - status.md
  - decisions.md
  - session-log.md
  - status.md
  - deadlines.md
- entities/
  - [list entity files]
- inbox/
  - [list unprocessed count]
- archive/
  - [list archive files]
- scratch/
  - [list scratch files]
- [domain folders...]

## Recent Activity

- Last dream run: [ISO timestamp]
- Files modified since last run: N
- Inbox items processed: N
- Tasks archived: N
- Links fixed: N

## Dream Protocol Stats

- Total runs: N
- Last verification: pass/fail (N issues)
- Session signal extracted: N items
- Domains checked: N
```

Ensure all links in manifest resolve. Remove archived projects from active domain list.

