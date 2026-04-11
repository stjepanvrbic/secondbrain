---
name: deadline-check
description: >
  This skill should be used when the "deadline tracker scheduled task" runs
  (midday ~1pm), or when the user asks to "check deadlines" or "what's overdue".
  Lightweight urgency scan — categorizes tasks by deadline proximity, auto-promotes
  urgent items, writes findings to status. Scheduled task only.
metadata:
  version: "3.3.5"
---

# Core Rule

Midday urgency check. Gather deadline data, categorize by proximity, auto-promote tasks approaching deadlines, write findings to status.

# Prerequisites

1. Read `_MANIFEST.md` for current vault state.
2. For vault navigation, read `@${CLAUDE_PLUGIN_ROOT}/references/vault-navigation.md`.

# Steps

## 1. Gather Deadline Data

Query brain/status.md and brain/deadlines.md for all open tasks with [due::] fields. Also check brain/battle-plan-milestones.md for milestone deadlines.

## 2. Categorize by Urgency

| Category | Criteria | Action |
|----------|----------|--------|
| **OVERDUE** | due < today | Flag `[OVERDUE]`, move to top of URGENT section |
| **CRITICAL** | due within 48 hours | Write warning to brain/status.md |
| **URGENT** | due within 7 days | Auto-promote to "URGENT (This Week)" if not already there |
| **UPCOMING** | due within 14 days | Note for awareness, no action |

## 3. Auto-Promote

For tasks due within 7 days in "This Week", "Ongoing", or "Someday":
- Move to "URGENT (This Week)" in brain/status.md
- Add note: `[auto-promoted: due within 7d — DATE]`

For overdue tasks: add `[OVERDUE]` flag, move to top of URGENT.

## 4. Write Findings to Status

Write to brain/status.md:

```markdown
## Deadline Check — [ISO timestamp]

**Overdue:** X tasks
**Critical (48h):** X tasks
**Urgent (7d):** X tasks
**Promotions made:** X tasks moved to URGENT

- OVERDUE: [task] [[entity]] — was due [date]
- CRITICAL: [task] [[entity]] — due [date]
```

# Error Handling

- No tasks with due dates: note "no deadline-bearing tasks found — tasks may need [due::] fields"
