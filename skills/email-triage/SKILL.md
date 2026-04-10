---
name: email-triage
description: >
  This skill should be used when the user asks to "check my email", "any new emails?",
  "triage inbox", "scan my email", or when a scheduled email triage task runs
  (morning, midday, evening). Fully reads and triages ALL unread emails until
  inbox reaches zero unread. Categorizes, extracts action items, labels important
  emails, archives noise. Inbox must be clean when done.
metadata:
  version: "2.4.0"
---

# Core Rule

Triage ALL unread emails to zero. **Every single email must be individually fetched and read by an agent** — no keyword filtering, no script-based categorization, no shortcuts. Each email is read in full via `get_email`, understood, and categorized by an agent. Extract action items, entities, and commitments into the vault. Label important emails. Archive noise. Zero unread when done.

Be careful — don't gloss over emails. Missing an important email can be disastrous.

# FORBIDDEN

- **Deleting emails** — NEVER delete. Only read, label, and archive.
- **Writing Python/Bash scripts to categorize emails** — no keyword lists, no regex, no sender-pattern matching, no automated heuristics
- **Categorizing from search result summaries** — `search_emails` returns only metadata (subject, sender, date). You CANNOT categorize from this. You MUST call `get_email` to read the full content of each email before categorizing.
- **Skipping reading an email for any reason**
- **Dumping full email contents to the user**
- **Finishing with unread emails remaining**

# Prerequisites

1. Read `_MANIFEST.md` for current vault state.
2. For vault navigation, read `@${CLAUDE_PLUGIN_ROOT}/references/vault-navigation.md`.
3. For content templates, read `@${CLAUDE_PLUGIN_ROOT}/references/templates.md`.

# Triage Algorithm

```
1. SEARCH for unread email IDs: is:unread (maxResults: 500, paginate if more)
   The search result is ONLY for collecting email IDs.
   Do NOT categorize from the search results — they lack full content.

2. Split the email IDs into batches (~20 per batch).
   Dispatch subagents to process batches in parallel.
   Each subagent is invoked with the email-triage skill.

3. Each subagent, for EACH email ID in its batch:
   a. FETCH the full email via get_email using the email ID
   b. READ and understand the full content
   c. CATEGORIZE based on understanding the content:
      - URGENT: needs action today (deadline, time-sensitive, blocker)
      - IMPORTANT: needs action this week, or worth referencing later
      - INFORMATIONAL: FYI only (status update, newsletter worth reading)
      - NOISE: marketing, promo, irrelevant — only after reading it
   d. ACT on the categorization (see below)

4. For URGENT and IMPORTANT emails — FULLY INGEST all signal:
   a. Tasks/action items → brain/status.md with full metadata
   b. Deadlines/dates mentioned → brain/deadlines.md with [due::] fields
   c. Decisions or agreements → brain/decisions.md as atomic section
   d. New people, companies, contacts → create entities/{name}.md
   e. Updates about known entities → update their entity file
   f. Relationships between entities → add [[wikilinks]] in both directions
   g. Domain-specific info → route to appropriate {domain}/ file
   h. Apply "Important" label to the email
   i. Mark as read
   Treat every email like a brain dump — extract EVERYTHING worth persisting.

5. For INFORMATIONAL emails:
   a. If it contains any entities, dates, or facts worth persisting, ingest them
   b. If worth referencing later, apply "Important" label
   c. Mark as read

6. For NOISE emails:
   a. Archive immediately
   b. Mark as read

7. VERIFY: search is:unread again.
   If unread emails remain, go back to step 1.
   Do NOT finish until unread count is 0.
```

# Labels

- **Important** — apply to any email the user might need to reference or look at again. This includes: emails with action items, emails containing decisions or agreements, emails with reference information (confirmations, receipts, account details), emails from priority senders, anything that isn't pure noise.

# Priority Sender Awareness

Load priority context from vault entities. High-priority senders get immediate attention:
- Immigration-related contacts (attorneys, USCIS, employers)
- Active project stakeholders
- Family and close contacts
- Financial institutions with time-sensitive matters

Use the Obsidian MCP (`vault_list` on `entities/`) to get the list of known contacts. If sender matches a known entity, link to them.

# Action Item Format

Every extracted action item follows ingest patterns:

```markdown
- [ ] [Action description] [[entities/sender-name]] #domain [due:: 2026-MM-DD] [energy:: low|medium|high] [est:: 15min|30min|1hr]
```

# Triage Window Behavior

**Morning triage:** Full scan, all unread. Most thorough.
**Midday triage:** Catch-up scan since morning. Same categorization.
**Evening triage:** Same process but bias toward deferring non-urgent items to tomorrow. Still triage to zero unread.

All windows must end with zero unread emails.

# Response Style

Brief summary only:
```
Email triage done — 0 unread remaining.
Action items: 3 added to status.
Labeled important: 7 emails.
Archived: 12 emails.
Urgent: Follow up with [[entities/mmh]] on LCA status (due tomorrow).
```

# Error Handling

- **Gmail MCP unavailable**: Note that email triage requires Gmail access, suggest user check connection
- **Unknown sender with action item**: Create minimal entity file, flag for review
- **Duplicate action item**: Check status.md before adding, skip if already exists
- **Ambiguous urgency**: Default to IMPORTANT, not URGENT

# Implementation Notes

- `search_emails` is for getting email IDs only — set maxResults to 500, paginate if needed
- `get_email` is for reading full email content — call this for EVERY email before categorizing
- Batch size for subagents: ~20 emails per subagent
- Entity names: kebab-case filenames, wikilink as full name
- All vault writes use [[wikilinks]] — no unlinked information
- Timestamps in local time (no UTC)
- Apply "Important" label via `modify_labels`
