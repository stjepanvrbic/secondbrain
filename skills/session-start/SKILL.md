---
name: session-start
description: >
  This skill should be used at the "start of every session", when a SessionStart
  hook fires, or when the user asks to "refresh context", "reload vault state",
  or "update context". Loads hot context including status, urgent commitments,
  deadlines, and focus domain. Morning mode builds a day plan energy-matched
  to the user's rhythm.
metadata:
  version: "3.1.1"
---

# Core Rule

**MANDATORY, FIRST ACTION OF EVERY SESSION.** Do NOT respond to the user's message until context is loaded. Ensure Claude operates with current awareness of goals, urgency, and where work left off.

# MUST READ EVERY SESSION, NON-NEGOTIABLE
1. Read `_MANIFEST.md` for current vault state.
2. For vault navigation, read `@${CLAUDE_PLUGIN_ROOT}/references/vault-navigation.md`.

# First Actions (in order)
1. `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/auto_update.py` — pull latest plugin version if available (fast, silent if up to date)
2. `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/vault_guide.py ${VAULT_PATH}` — load dynamic vault context

# Output and Response Style

## Non-Morning Sessions
- Internal context summary only (not shown to user)
- Inform responses with loaded context
- Respond naturally to whatever the user actually said

## Morning Sessions (First session ~10:30am EST)
- Scan overnight status (inbox entries, status updates from scheduled tasks)
- Extract urgency: what MUST happen today?
- Build day plan: 3-5 tasks matched to energy rhythm
  - Morning (10:30am-12pm): planning, admin, email, decisions
  - Afternoon (12pm-6pm): deep work, studying, coding, complex tasks
  - Evening (6pm-7:30pm): low energy, quick tasks, light admin
- Present as "Today's plan:" with estimated times
- Serve the first task immediately
- DO NOT ask for input — present and move forward

# Re-Invocation Rules (during same session)

**MUST re-load if ANY of:**
- >30 minutes since last context load
- User references something not in current context
- Vault state may have changed (another session or scheduled task ran)
- User says "refresh", "update context", or similar

**FORBIDDEN:** Operating for extended periods without refreshing context.

# Morning Mode Algorithm

```
IF first session of day around 10:30am EST:
  1. Load D1-D7 + conditional loading
  2. Scan D3 (inbox) for overnight entries
  3. Check D1 (status) for overnight changes
  4. Extract: what's urgent? what's blocked? what moved?
  5. Review D6 (milestones) for this week's focus
  6. Reason: Given D7 (energy rhythm), D6 (goals), D2 (urgency/deadlines):
     - Pick 3-5 tasks
     - Match to energy windows
     - Estimate time each
     - Order by priority + energy match
  7. Present as "Today's plan" with task names + times
  8. Serve first task: "Let's start with: [task]. Step 1: [micro-step]"
  9. Wait for input OR task completion before serving next
ELSE:
  1. Load D1-D4 + conditional loading
  2. Present context internally
  3. Respond to user normally
END
```

# Error Handling

- If brain/status.md missing: create minimal (timestamp, current focus = "unknown")
- If brain/status.md has no tasks: note absence, load brain/deadlines.md instead
- If domain file missing but referenced in status.md: note absence, do not error
- If entity files missing: load from glossary.md as fallback
- Broken wikilinks: detect, log, continue (do not block context load)
- Vault unreachable: Check if Obsidian is running. If not, attempt to launch it:
  - macOS: `open -a Obsidian`
  - Linux: `nohup obsidian &>/dev/null &`
  - Windows: `Start-Process Obsidian`
  Wait up to 10 seconds for the MCP server to become available, then retry.
  If still unreachable: "Vault is inaccessible — Obsidian may not be running. Launch Obsidian and try again."

# Implementation Notes

- Check current time against EST timezone
- "First session" = no session-start has run today yet
- Energy mapping is REFERENCE only — whats-next skill applies it for dispatch
- Broken wikilinks: log but continue loading (do not fail)
