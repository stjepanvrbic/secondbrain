---
name: vault-review
description: >
  This skill should be used when the user asks "how am I doing?", "what's overdue?",
  "review my week", "audit my tasks", "check my deadlines", or when a scheduled
  deadline check or weekly review task runs. Supports focused deadline reviews
  and full weekly audits with auto-promotion of urgent tasks.
metadata:
  version: "3.5.17"
---

# Core Rule

Review vault state, produce actionable updates. Read status, deadlines, goals, and project status. Auto-promote tasks by urgency, flag problems, and write updates to brain/status.md. Operate in focused mode (deadlines only) or full audit mode (weekly review). Use `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify_vault.py "${VAULT_PATH}" --json` for validation.

# Prerequisites
1. Read `_MANIFEST.md` for current vault state.
2. For vault navigation, read `@${CLAUDE_PLUGIN_ROOT}/references/vault-navigation.md`.

# Review Modes

## Focused: Deadline Review

Used by deadline-tracker scheduled task. Quick check focused on time-sensitive items.

```
1. Query all tasks with [due::] fields via DQL
2. Categorize by urgency:
   - OVERDUE (past due) → flag in brain/status.md
   - CRITICAL (within 48 hours) → write warning to brain/status.md
   - URGENT (within 7 days) → auto-promote to "Urgent This Week" in status.md
3. Write all changes to vault
4. Brief summary of findings
```

## Full: Weekly Audit

Used by weekly-review scheduled task. Comprehensive audit of all life threads.

```
1. GOALS CHECK — on track toward each goal? Flag what needs attention
2. MILESTONE CHECK — what was hit/missed this week?
3. COMMITMENTS AUDIT — every section: undone urgent, carryover, stale, unblockable, promotable, wins
4. DEADLINE CHECK — what's coming next week? Auto-promote as needed
5. DOMAIN STATUS — scan all domain folders, flag completed projects
6. DECISION CHECK — any pending decisions that need making?
7. ENTITY FOLLOW-UPS — contacts needing follow-up
8. BUILD NEXT WEEK'S PLAN — top 3 priorities, blockers to resolve, write to brain/status.md
9. PRESENT REVIEW — what went well, what needs attention, plan for next week, direct about delays
```

# Auto-Promotion Rules

- **Due within 7 days** + currently in "This Week" or "Someday" → move to "Urgent This Week"
- **Overdue** → add "[OVERDUE]" flag, move to top of "Urgent This Week"
- **Stale >14 days** → add "[STALE]" flag, consider moving to "Someday"

Add note to promoted tasks: `[auto-promoted by vault-review — DATE]`

# Status Updates

All review findings write to brain/status.md:

```markdown
## Vault Review — [ISO timestamp]

**Review type:** [focused/full]
**Overdue:** X tasks
**Critical (48h):** X tasks
**Urgent (7d):** X tasks
**Stale:** X tasks
**Promotions:** X tasks moved to Urgent This Week
**Next focus:** [[domain-name]]
```

# Response Style

**Focused review:** Brief, numbers-driven.
```
Deadline check done — 1 overdue, 2 critical, 3 promoted to urgent.
Overdue: Follow up with [[entities/mmh]] (was due Mar 22).
```

**Full review:** Structured but concise. Present findings, then the plan.

**FORBIDDEN:**
- Presenting options ("you could do A or B") — decide and present
- Hiding problems — be direct about what's behind schedule
- Verbose narration — bullet points, not paragraphs

# Error Handling

- **No tasks with deadlines**: Note "no deadline-bearing tasks found" — may indicate tasks need [due::] fields
- **Goals file missing**: Skip goals check, note for manual review
- **Domain folder empty**: Skip, note as potential cleanup candidate

# Implementation Notes

- All timestamps in local time (no UTC)
- [[Wikilinks]] required on all status updates
- Promotions logged so dream-protocol doesn't re-promote
- Weekly review should run Sunday to set up Monday
- Deadline review is lightweight — runs midday daily
