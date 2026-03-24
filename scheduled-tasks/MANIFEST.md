# Bundled Scheduled Tasks

This file is consumed by `/secondbrain:init` during plugin setup.
Each entry: task name, default cron, plugin skill, opt-in default.

| Task | Cron | Skill | Default |
|---|---|---|---|
| morning-briefing | 30 10 * * * | /secondbrain:morning-brief | on |
| deadline-tracker | 0 13 * * * | /secondbrain:deadline-check | on |
| email-triage | 0 9 * * 1-5 | /secondbrain:email-triage | on (requires Gmail MCP) |
| end-of-day-capture | 30 19 * * * | /secondbrain:end-of-day | on |
| weekly-review | 0 20 * * 0 | /secondbrain:weekly-review | on |
| dream-protocol | 0 2 * * * | /secondbrain:dream-protocol | on |

## How `/init` uses this file

**Claude Code:**
1. Reads each row
2. For each opted-in task, calls `CronCreate` with the cron schedule and the prompt `Run <skill>`
3. Also drops a copy of the corresponding `scheduled-tasks/<task-name>/SKILL.md` into `~/Documents/Claude/Scheduled/<task-name>/SKILL.md` so the task is discoverable by name

**Claude Cowork:**
1. `CronCreate` is not available — instead, prints a list of `/schedule` commands the user pastes into the Cowork chat manually
2. Drops `scheduled-tasks/<task-name>/SKILL.md` into `<workspace>/.scheduled-tasks/` for skill discovery

## Default cron meanings

| Cron | When |
|---|---|
| `30 10 * * *` | 10:30am every day |
| `0 13 * * *` | 1:00pm every day |
| `0 9 * * 1-5` | 9:00am on weekdays (Mon-Fri) |
| `30 19 * * *` | 7:30pm every day |
| `0 20 * * 0` | 8:00pm on Sundays |
| `0 2 * * *` | 2:00am every day |

Adjust these in `/secondbrain:init` during setup if your daily rhythm is different.
