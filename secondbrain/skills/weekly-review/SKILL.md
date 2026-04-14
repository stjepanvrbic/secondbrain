---
name: weekly-review
description: >
  This skill should be used when the "weekly review scheduled task" runs
  (Sunday ~8pm), or when the user asks to "review my week" or "weekly audit".
  Full audit of all life threads — goals, milestones, commitments, deadlines,
  domains, decisions, entity follow-ups. Builds next week's plan. Scheduled task only.
metadata:
  version: "3.5.25"
---

# Core Rule

Full vault audit. Review every life thread, celebrate wins, be direct about what's behind schedule, build next week's plan. No sugarcoating, no options — decide and present.

# Prerequisites

1. Read `_MANIFEST.md` for current vault state.
2. For vault navigation, read `@${CLAUDE_PLUGIN_ROOT}/references/vault-navigation.md`.
3. For content templates, read `@${CLAUDE_PLUGIN_ROOT}/references/templates.md`.
4. For shared write rules, read `@${CLAUDE_PLUGIN_ROOT}/references/ingestion-rules.md`.
5. For the named DQL queries used in the audit, read `@${CLAUDE_PLUGIN_ROOT}/references/dql-patterns.md`.
6. For script commands (`verify_vault.py`, `rebuild_manifest.py`), read `@${CLAUDE_PLUGIN_ROOT}/references/script-invocations.md`.

# Steps

## 1. Goals Check

Read brain/goals.md. For each goal: on track? Anything moved this week? Flag goals needing attention.

## 2. Milestone Check

Read brain/battle-plan-milestones.md. What was this week's target? Hit or missed? Flag missed milestones.

## 3. Commitments Audit (every section)

Read brain/status.md and audit each section:

| Section | Check | Query |
|---------|-------|-------|
| URGENT (This Week) | Anything left undone? Carry forward or escalate? | — |
| This Week | What carried over? Promote or demote? | — |
| Ongoing | Anything stale (>2 weeks untouched)? Flag or move to Someday | `stale-tasks` |
| Waiting On | Anything unblockable or needing follow-up? | — |
| Someday | Anything worth promoting based on goals/deadlines? | — |
| Done (Recent) | Celebrate wins. Archive items >7 days old | `archive-candidates` |

Named queries are defined in `@${CLAUDE_PLUGIN_ROOT}/references/dql-patterns.md`.

## 4. Deadline Check

What's coming next week? Run the `approaching-deadlines` query to surface tasks due within 7 days and auto-promote them. Run the `overdue-tasks` query and flag anything past due.

## 5. Domain Status

Scan all domain folders. Read each domain's index.md. Flag completed projects for archiving.

## 6. Decision Check

Read brain/decisions.md. Any pending decisions needed this week? Any decided items needing follow-through?

## 7. Entity Follow-Ups

Scan entities/ for contacts needing follow-up. Check if entity-linked tasks are overdue.

## 8. Build Next Week's Plan

Based on goals + deadlines + milestones:
- Top 3 priorities for next week
- Blockers to resolve first
- Write to brain/status.md under "Next Week Focus"

## 9. Write Review to Vault

Write to brain/status.md:

```markdown
## Weekly Review — [DATE]

**What went well:**
- [Wins with [[wikilinks]]]

**What needs attention:**
- [Items behind schedule with [[wikilinks]]]

**Missed milestones:**
- [If any]

**Next week's priorities:**
1. [Priority 1] — [[domain]]
2. [Priority 2] — [[domain]]
3. [Priority 3] — [[domain]]

**Blockers to resolve first:**
- [Blocker] — [[entity]]
```

# Presentation

Present structured but concise:
- What went well (celebrate)
- What needs attention (be direct)
- Plan for next week
- If something is seriously behind schedule, say so plainly

# Post-Write Validation

Run the standard post-write validation block from
`@${CLAUDE_PLUGIN_ROOT}/references/script-invocations.md` after ALL writes
are complete. Then run `rebuild_manifest.py` (also documented there) to
refresh `_MANIFEST.md` from the new vault state.

# Forbidden Actions

- Presenting options ("you could do A or B") — decide and present
- Hiding problems or being overly positive
- Verbose paragraphs — bullet points, not prose
