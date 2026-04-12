# {{USER_NAME}}'s Profile

> This file is your second brain's picture of you. `/secondbrain:init` seeds
> it with a handful of defaults; everything else builds up naturally from
> conversation. Edit anything here by hand whenever you want — the agent
> re-reads it at every session start.

---

## Who I Am

- **Name:** {{USER_NAME}}
- **Current role:** {{USER_ROLE}}
- **Next role / upcoming change:** {{USER_NEXT_ROLE}}
- **Partner / key relationships:** {{USER_PARTNER}}

(One-line is fine. Add more as the agent learns it.)

---

## Daily Rhythms

- **Wakes:** {{WAKEUP_TIME}}
- **Morning window** ({{MORNING_WINDOW}}): low-to-medium energy — planning, admin, decisions, email
- **Afternoon window** ({{AFTERNOON_WINDOW}}): high energy — deep work, studying, coding, complex analysis
- **Evening window** ({{EVENING_WINDOW}}): low energy — quick tasks, light admin, winding down

The morning briefing at {{WAKEUP_TIME}} is the highest-leverage habit — it's how the agent builds today's plan matched to these windows. Adjust the windows to match your actual rhythm; `whats-next` and `morning-brief` both read this section.

---

## Preferences (Static — Confirmed)

- {{USER_PREFERENCES}}
- Direct, no-fluff communication — no preamble, no filler
- Have a spine: push back when the user is wrong, don't just agree
- Work in Pomodoro-style blocks (~20 min focus, 5-10 min break)
- Hates context switching — batch by domain wherever possible
- Brain dumps freely — auto-process without interrupting

(These are starter defaults from init. Edit to match your actual preferences. New learned preferences get appended below by the agent.)

---

## Domain Context

(Add domain-specific context as it accumulates: work projects, study
threads, the gym, the band, whatever takes real mental space. The agent
routes domain-tagged tasks against this section.)

- Work: _fill me in_
- Personal: _fill me in_

---

## Learned Preferences

(The agent appends here whenever it learns a new preference mid-session.
Things like "prefers morning meetings over afternoon", "hates being asked
twice before acting", "wants the bike ride route on Saturdays".)

_No learned preferences yet._

---

*Seeded by `/secondbrain:init` — edit freely. The agent re-reads this every session.*
