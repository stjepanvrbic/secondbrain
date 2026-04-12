# Content & File Templates

> Canonical templates for all content types in the vault.
> Use these whenever creating new files or entries. Consistency is non-negotiable.

---

## Content Entry Templates

### Task

Task template and field order are defined in `@${CLAUDE_PLUGIN_ROOT}/references/ingestion-rules.md` (Rule 2).

### Decision Entry

Append as atomic section to `brain/decisions.md`:

```markdown
## Decision Title — YYYY-MM-DD

**Context:** [What prompted this decision] [[related-entity]]
**Decision:** [What was decided]
**Rationale:** [Why — one to three sentences]
**Implications:** [[affected-domain]], [[affected-entity]]
```

### Session Log Entry

Prepend to `brain/session-log.md` (reverse chronological):

```markdown
## Session — YYYY-MM-DDTHH:MM:SS

**Duration:** ~X minutes
**Domains:** [[domain1]], [[domain2]]
**Work done:**
- Specific accomplishment [[entity]]

**Decisions made:**
- Decision: [[brain/decisions#section]]

**Blockers identified:**
- Blocker: [[entity]]

**Next session focus:** [[domain-name]]
```

### Status Update

Replace/append in `brain/status.md`:

```markdown
## Last Session Summary — YYYY-MM-DDTHH:MM:SS

**Domain(s) worked on:** [[domain-name]]
**Key accomplishments:**
- Item [[entity]]

**Where I left off:**
[Exactly what was in progress and the next step]

**Current focus domain:** [[domain-name]]
**Status:** on-track | needs-attention | blocked
```

### Inbox Processing

After processing an inbox file, move it to `archive/inbox/`:

1. Route all content from the inbox file to vault destinations (status.md, decisions.md, entities, etc.)
2. Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/archive_inbox.py ${VAULT_PATH}` to move processed files to `archive/inbox/`
3. NEVER modify the original inbox file in place
4. NEVER delete inbox files

### log.md Entry (Karpathy-style append-only chronological log)

Append to the end of `log.md` (NEVER prepend, NEVER edit existing entries):

```markdown
## [YYYY-MM-DD HH:MM] <operation> | <title>
<one-to-three line body summarizing what happened>
```

**Operations:** `ingest`, `session-start`, `session-end`, `dream-protocol`, `weekly-review`, `deadline-check`, `morning-brief`, `end-of-day`, `email-triage`, `init`, `manual`

**Examples:**

```markdown
## [2026-04-09 10:35] ingest | Brain dump from this morning
Routed 3 tasks to status, created entities/jane-doe, updated brain/status with new blocker.

## [2026-04-09 02:00] dream-protocol | Run #22
Processed 0 inbox items, promoted 1 deadline, fixed 4 wikilinks, rebuilt manifest content catalog.

## [2026-04-09 19:30] session-end | Triage + planning session
Closed 2 tasks, added 1 decision (move-in date), flagged 1 blocker on financing.
```

**Why this format:**
- `## [YYYY-MM-DD HH:MM]` is greppable: `grep "^## \[2026-04" log.md` for all April entries
- `<operation>` is the second field: `grep "^## .* ingest " log.md` for all ingests
- `|` separator makes the title visually distinct
- Append-only means history is reconstructable forever

---

## File Scaffolding Templates

> For creating missing vault structure files. Only create if the file does not exist.

### brain/status.md

```markdown
---
type: status
updated: [TODAY]
---

# Status

## Current Focus
Not set — start a session or run `/secondbrain:whats-next` to begin.

## Blockers
None identified.
```

### brain/commitments.md (DEPRECATED)

> DEPRECATED: commitments.md has been replaced by brain/status.md as the single source of truth for tasks. Do not create new commitments.md files. Existing ones are archived.

### brain/deadlines.md

```markdown
---
type: deadlines
updated: [TODAY]
---

# Deadlines

> Hard dates and countdowns. Dream protocol auto-promotes when within 7 days.
```

### brain/goals.md

```markdown
---
type: goals
updated: [TODAY]
---

# Goals

> Life goals and priorities.
```

### brain/decisions.md

```markdown
---
type: decisions
updated: [TODAY]
---

# Decisions

> Decisions made + rationale + context links.
```

### brain/session-log.md

```markdown
---
type: session-log
updated: [TODAY]
---

# Session Log

> Reverse chronological session history.
```

### log.md (vault root, Karpathy-style append-only)

```markdown
# Vault Log

> Append-only chronological record. New entries at the bottom.
> Format: `## [YYYY-MM-DD HH:MM] <operation> | <title>`
> See `references/templates.md` for the full format spec.
>
> Query examples:
> - `grep "^## \[" log.md | tail -20` — last 20 entries
> - `grep "^## .* ingest " log.md` — all ingests
> - `grep "^## \[YYYY-MM" log.md` — entries for a specific month

---

## [INIT_TIMESTAMP] init | Vault initialized
Initial scaffolding by /secondbrain:init
```

### me/profile.md

```markdown
---
type: self-knowledge
updated: [TODAY]
---

# Profile

> Personality, communication style, preferences.
```

### me/energy.md

```markdown
---
type: self-knowledge
updated: [TODAY]
---

# Energy Patterns

> Daily energy rhythms.
```

### me/adhd-protocol.md

```markdown
---
type: self-knowledge
updated: [TODAY]
---

# ADHD Protocol

> Strategies that work.
```

### me/dopamine-menu.md

```markdown
---
type: self-knowledge
updated: [TODAY]
---

# Dopamine Menu

## Instant (< 5 min)
- Stand up, stretch
- Get water

## Short (5-15 min)
- Walk around the block
- Music break

## Momentum Builders (15-30 min)
- Light reading
- Sketch/doodle
```

### entities/directory.md

```markdown
---
type: directory
updated: [TODAY]
---

# Entity Directory

| Name | Type | Domains | File |
|------|------|---------|------|
```

### glossary.md

```markdown
---
type: glossary
updated: [TODAY]
---

# Glossary

| Term | Meaning |
|------|---------|
```

### Entity File (entities/{kebab-name}.md)

```markdown
---
type: person | company | organization | place | tool
domains: [domain1, domain2]
relationship: [how user relates to this entity]
created: [TODAY]
updated: [TODAY]
---

# [Entity Full Name]

## Overview
[1-2 sentence description]

## Domains Involved
[[domain1]], [[domain2]]

## Relationships
- [[related-entity]]
```

---

## _MANIFEST.md Structure

> Dream protocol rebuilds this nightly. This is the vault index — read it FIRST.

```markdown
---
type: manifest
updated: [TIMESTAMP]
last-dream-run: [TIMESTAMP]
---

# Vault Manifest

> Source of truth for vault state. Read this FIRST in every skill.

## Vault Health

| Metric | Value |
|--------|-------|
| Total files | N |
| Total entities | N |
| Open tasks | N |
| Overdue tasks | N |
| Unprocessed inbox | N |
| Broken wikilinks | N |
| Last dream run | [TIMESTAMP] |
| Last session | [TIMESTAMP] |

## Active Domains

| Domain | Files | Open Tasks | Status |
|--------|-------|------------|--------|
| domain-name | N | N | active/blocked/complete |

## File Tree

### brain/
- status.md
- [... all files]

### entities/
- directory.md
- [... all entity files]

### inbox/
- [unprocessed files or empty]

### me/
- [... all files]

### {domain}/
- [... per domain]

### archive/
- [... archived content]

## Recent Activity (last 7 days)

- YYYY-MM-DD: [summary of what happened]

## Dream Protocol Stats (last run)

- Inbox processed: N
- Tasks archived: N
- Tasks promoted: N
- Links fixed: N
- Verification: pass/N issues
```
