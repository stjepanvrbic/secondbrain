# Dream Protocol Execution Pipeline

> Detailed procedures for each phase of the nightly vault maintenance run.

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

Use DQL queries to find what needs attention. Don't exhaustively read everything — query narrowly for what matters.

### 2.1 Unprocessed Inbox Files

```
DQL: TABLE file.name, file.ctime FROM "inbox" WHERE processed != true SORT file.ctime ASC
```

Flag each unprocessed file for routing in Phase 3.

### 2.2 Session Log Signal Extraction

```
READ brain/session-log.md (entries since last dream run only)
```

Scan each session entry for:

- **Unprocessed TODOs and action items** — tasks mentioned but not yet in brain/commitments.md
- **Decisions made** — choices, conclusions, or direction changes that should be in brain/decisions.md
- **New entities mentioned** — people, companies, tools, concepts referenced but without an entities/ file
- **Context and insights** — observations, learnings, or status updates worth persisting to the relevant vault file
- **Status changes** — focus shifts, priority changes, or blockers not yet reflected in brain/status.md

Collect all extracted signal into a working list for Phase 3 processing.

### 2.3 Stale Tasks

```
DQL: TASK FROM "brain/commitments" WHERE !done AND (date(today) - file.mtime) > dur(14 days)
```

Note: also cross-reference with brain/status.md for recent activity mentions — a task may appear stale by file modification but was discussed recently.

### 2.4 Deadline Proximity

```
DQL: TASK FROM "brain/commitments" WHERE due AND (due - date(today)) <= dur(7 days) AND !done
```

Check whether each result is already in the "Urgent This Week" section. Flag those that are not for promotion in Phase 3.

### 2.5 Archive Candidates

```
DQL: TASK FROM "brain/commitments" WHERE done AND (date(today) - done) > dur(7 days)
```

### 2.6 Broken Links & Orphans

```
FOR each file in **/*.md:
  - Check all [[wikilinks]] resolve to existing files or sections
  - Flag files with zero outgoing links as orphans
```

### 2.7 Contradicted Content

Scan for vault content that contradicts current state:
- Status entries that no longer reflect reality (e.g., "blocked by X" when X is resolved)
- Task descriptions with outdated context
- Entity files with stale information

---

## Phase 3 — Consolidate

Process everything gathered in Phase 2. Each procedure below corresponds to signal identified during gathering.

### 3.1 Inbox Processing

```
FOR each unprocessed file in inbox/:
  1. Read file contents
  2. Route content using ingest routing rules:
     - Tasks       → brain/commitments.md
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
  - TODOs/action items → route to brain/commitments.md (appropriate section)
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
  3. Remove from active commitments.md
ENDFOR
```

### 3.9 Hot Cache Update (brain/status.md)

```
REFRESH brain/status.md:
  1. Clear stale "Last Ingest" section
  2. Update "Current Focus" based on last session log
  3. Extract "Urgent This Week" count from commitments.md
  4. Extract blockers from commitments.md [blocked-by::] fields
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
     a. Count tasks in brain/commitments.md tagged #domain
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

### 3.12 Contradiction Cleanup

```
FOR each contradicted item flagged in Phase 2.7:
  - Delete the outdated/contradicted fact at the source
  - If new info disproves old, update the original file directly
  - Do NOT leave both versions — resolve to the current truth
ENDFOR
```

---

## Phase 4 — Verify & Index

Verification runs BEFORE manifest rebuild so the manifest reflects the verified state of the vault.

### 4.1 Vault Verification (Gate)

Dream protocol is NOT complete until this passes.

Run the verification script:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/dream-protocol/scripts/verify-vault.py "${VAULT_PATH:-$HOME/vault}" --json
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
  - commitments.md
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

