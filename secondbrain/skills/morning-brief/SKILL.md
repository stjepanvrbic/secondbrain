---
name: morning-brief
description: >
  This skill should be used when the "morning briefing scheduled task" runs
  (~10:30am), or when the user asks to "build my day plan" or "morning brief".
  Loads vault context, processes overnight inbox, builds an energy-matched
  3-5 task day plan, and presents it directly. Scheduled task only.
metadata:
  version: "3.6.1"
---

# Core Rule

Build and present an energy-matched day plan. Load context, process overnight inbox, select 3-5 tasks, write the plan to vault, serve the first task. No options, no preamble — direct presentation.

# Prerequisites

1. Read `_MANIFEST.md` for current vault state.
2. Read `me/profile.md` for the user's daily rhythms — the energy-window table below is seeded with defaults, but profile.md is the source of truth.
3. For vault navigation, read `@${CLAUDE_PLUGIN_ROOT}/references/vault-navigation.md`.
4. For content templates, read `@${CLAUDE_PLUGIN_ROOT}/references/templates.md`.

# Steps

## 1. Load Context

Read brain/status.md (all open tasks), brain/goals.md, brain/battle-plan-milestones.md, and me/profile.md (for rhythms). Use DQL queries to get urgent/upcoming tasks efficiently.

## 2. Process Overnight Inbox

If unprocessed inbox files exist, route them to vault using ingest rules:
- Tasks → brain/status.md with full metadata + [[wikilinks]]
- Decisions → brain/decisions.md
- Entity info → entities/{name}.md
- Mark processed in frontmatter. NEVER delete inbox files.

## 3. Build Day Plan (3-5 tasks)

Match tasks to energy rhythm:

| Window | Time | Energy | Best For |
|--------|------|--------|----------|
| Morning | 10:30am-12pm | Low-medium | Planning, admin, email, decisions |
| Afternoon | 12pm-6pm | High | Deep work, studying, coding, complex tasks |
| Evening | 6pm-7:30pm | Low | Quick tasks, light admin |

Selection:
1. URGENT (This Week) tasks come first — always
2. Deadline within 48 hours → serve it, no exceptions
3. Match [energy::] to time window
4. Batch by domain to minimize context switching
5. If task [est::] exceeds available window, defer to next

## 4. Write Plan to Vault

Write to brain/status.md under "Today's Plan — [DATE]":

```markdown
## Today's Plan — [DATE]

**Morning (10:30am-12pm):**
- [ ] Task 1 [[entity]] [est:: Xmin]

**Afternoon (12pm-6pm):**
- [ ] Task 2 [[entity]] [est:: Xhr]

**Evening (6pm-7:30pm):**
- [ ] Task 3 [[entity]] [est:: Xmin]
```

## 5. Present

```
Today's plan:

Morning: [task 1] (Xmin), [task 2] (Xmin)
Afternoon: [task 3] (Xhr)
Evening: [task 4] (Xmin)

[If urgent]: ⚠ [urgent thing] needs attention today — [why].

Let's start with: [first task]. [One-sentence context or first micro-step.]
```

# Error Handling

- No urgent tasks: build plan from This Week + Ongoing, note "light day"
- All tasks blocked: identify blockers, serve blocker-resolution as first task
- Inbox processing fails: note unprocessed items, continue with plan

# Forbidden Actions

- Presenting options ("you could do A, B, or C")
- Asking what user wants to work on
- Verbose explanations of why each task was chosen
- More than 5 tasks in the plan
- Preamble or pleasantries
