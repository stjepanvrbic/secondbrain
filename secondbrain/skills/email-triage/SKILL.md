---
name: email-triage
description: >
  This skill should be used when the user asks to "check my email", "any new emails?",
  "triage inbox", "scan my email", or when a scheduled email triage task runs
  (morning, midday, evening). Fully reads and triages ALL unread emails until
  inbox reaches zero unread. Categorizes, extracts action items, labels important
  emails, archives noise. Inbox must be clean when done.
metadata:
  version: "3.3.5"
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
4. For shared write rules (wikilinks, task metadata field order, atomic sections, entity stubs), read `@${CLAUDE_PLUGIN_ROOT}/references/ingestion-rules.md`.
5. For script commands (`verify_vault.py`), read `@${CLAUDE_PLUGIN_ROOT}/references/script-invocations.md`.

# Crash-Safety Invariant (non-negotiable)

Gmail read-status is the **only** durable record that a given email was processed. Once an email is marked read (or labeled, or archived), the inbox scan on the next run will not return it. Therefore:

**No Gmail state mutation — no `modify_labels`, no archive, no mark-as-read — may happen for an email until all vault writes derived from that email have been flushed AND validated via `verify_vault.py --modified-only`.**

If a subagent crashes between "wrote signal to vault" and "marked read", the next triage run will re-fetch the email and idempotently re-ingest it — duplicate detection in the ingest paths (already required by "Duplicate action item: check status.md before adding" under Error Handling) absorbs the overlap. The reverse failure — marked-read-but-not-written — is unrecoverable and silently drops signal. Never allow the reverse order.

To cover crashes that land mid-batch (after some emails are already fully processed, and before the batch terminates cleanly), each subagent also writes a per-batch **in-flight manifest** at `scratch/email-triage-in-flight.md` before it touches any email, and deletes the manifest only after the whole batch has drained with zero outstanding errors. See "In-Flight Manifest" below.

# Triage Algorithm

```
1. RECOVER: read scratch/email-triage-in-flight.md via vault_read.
   If it exists, it is a leftover from a prior crashed run.
   Re-fetch every listed email ID (they may already be marked read in Gmail —
   re-ingest them anyway; signal-extraction is idempotent via the
   duplicate-task check in Error Handling) and run the per-email flow in
   step 4 below on each. Delete the manifest via vault_delete once all
   listed IDs have been reconciled.
   If it does not exist, skip to step 2.

2. SEARCH for unread email IDs: is:unread (maxResults: 500, paginate if more)
   The search result is ONLY for collecting email IDs.
   Do NOT categorize from the search results — they lack full content.

3. Split the email IDs into batches (~20 per batch).
   Dispatch subagents to process batches in parallel.
   Each subagent is invoked with the email-triage skill.

   Each subagent, BEFORE fetching any email:
     WRITE scratch/email-triage-in-flight.md via vault_create listing the
     email IDs in its batch (see "In-Flight Manifest" format below).
     If the file already exists from a sibling subagent or prior crash,
     append this batch's IDs to the existing list rather than overwriting.

4. Each subagent, for EACH email ID in its batch, in strict order:
   a. FETCH the full email via get_email using the email ID
   b. READ and understand the full content
   c. CATEGORIZE based on understanding the content:
      - URGENT: needs action today (deadline, time-sensitive, blocker)
      - IMPORTANT: needs action this week, or worth referencing later
      - INFORMATIONAL: FYI only (status update, newsletter worth reading)
      - NOISE: marketing, promo, irrelevant — only after reading it
   d. EXTRACT and WRITE all signal to the vault (see per-category routing
      in "Category Routing" below). All vault writes follow the shared
      rules in `@${CLAUDE_PLUGIN_ROOT}/references/ingestion-rules.md`.
      For NOISE, no vault write is required — skip to (e) with an empty
      touched-files list.
   e. VERIFY: run
         python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify_vault.py ${VAULT_PATH} \
           --modified-only <files-you-touched> --json
      on the files just written. If validation fails, FIX the issues in the
      vault first (see `@${CLAUDE_PLUGIN_ROOT}/references/script-invocations.md`
      "Standard Post-Write Validation Block") and re-verify. Do NOT proceed
      to step (f) until verification is clean. If NOISE (no files touched),
      this step is a no-op.
   f. ONLY NOW mutate Gmail state for this email:
      - URGENT / IMPORTANT / INFORMATIONAL-worth-saving → `modify_labels`
        with "Important", then mark as read.
      - INFORMATIONAL-ephemeral → mark as read.
      - NOISE → archive, then mark as read.

5. After a subagent's batch drains with zero outstanding errors, the
   subagent REMOVES its batch's IDs from scratch/email-triage-in-flight.md.
   When the manifest becomes empty, delete it entirely via vault_delete.

6. VERIFY: search is:unread again.
   If unread emails remain, go back to step 2.
   Do NOT finish until unread count is 0.
   scratch/email-triage-in-flight.md MUST NOT exist when the skill returns.
```

# Category Routing

The per-category routing is email-triage-specific and lives here (not in `ingestion-rules.md`, which covers shared mechanics only). All writes still obey the shared rules — wikilinks, inline metadata order, atomic sections, entity stubs.

**URGENT and IMPORTANT — FULLY INGEST all signal. Treat every email like a brain dump — extract EVERYTHING worth persisting:**

1. Tasks / action items → `brain/status.md` with full metadata
2. Deadlines / dates mentioned → `brain/deadlines.md` with `[due::]` fields
3. Decisions or agreements → `brain/decisions.md` as atomic section
4. New people, companies, contacts → create `entities/{name}.md`
5. Updates about known entities → update their entity file
6. Relationships between entities → add `[[wikilinks]]` in both directions
7. Domain-specific info → route to appropriate `{domain}/` file

**INFORMATIONAL:**

- If it contains any entities, dates, or facts worth persisting, ingest them per the URGENT/IMPORTANT routing above.
- If worth referencing later, flag for the "Important" label in step 4(f).

**NOISE:**

- No vault write. Flag for archive in step 4(f).

# In-Flight Manifest

`scratch/email-triage-in-flight.md` is a per-batch sidecar that makes a subagent crash recoverable. `scratch/` is a normal vault path — the immutability hook does NOT block `vault_create` / `vault_delete` there — so the subagent manages it directly via the Obsidian MCP, no helper script needed.

Format:

```markdown
---
created: <ISO timestamp>
---

# Email Triage In-Flight

Email IDs currently being processed by email-triage subagent(s). If this
file exists at the start of a triage run, it is the remnant of a crashed
prior run — re-fetch every ID below and re-ingest idempotently before
scanning for new unread mail.

- <gmail-message-id-1>
- <gmail-message-id-2>
- ...
```

Lifecycle:

1. **Write** with `vault_create` BEFORE fetching any email in the batch. If the file already exists (sibling batch running in parallel, or prior crash), `vault_read` it first and append this batch's IDs to the existing body instead of overwriting.
2. **Trim** incrementally: after each email is fully processed (vault write verified AND Gmail state mutated), remove its ID from the file via `vault_edit` or `vault_update`. This keeps the manifest a true "still in flight" list so crash recovery never re-processes finished work.
3. **Delete** with `vault_delete` once the file body contains no IDs.

The file must NOT exist when the email-triage skill returns successfully. Its presence is the signal that the prior run crashed.

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

Every extracted action item follows the shared task format in `@${CLAUDE_PLUGIN_ROOT}/references/ingestion-rules.md` — entity → #domain → [due::] → [energy::] → [est::]. Example for an email-derived task:

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
- **Duplicate action item**: Check status.md before adding, skip if already exists. This is also the idempotency anchor for crash recovery — an email that was written-but-not-marked-read before a crash will be re-fetched on the next run, and the duplicate check absorbs the second ingest without creating a duplicate task.
- **Ambiguous urgency**: Default to IMPORTANT, not URGENT
- **Verification fails mid-email**: `verify_vault.py --modified-only` returned errors on files just written. FIX the errors in the vault (missing entity stub, broken wikilink, etc.) and re-run verification before mutating any Gmail state for that email. Do NOT mark the email read and do NOT remove its ID from `scratch/email-triage-in-flight.md` until verification is clean. If the error is unrecoverable, leave the email unread and leave its ID in the manifest — the next triage run will retry.
- **In-flight manifest present at start**: Prior run crashed. Re-fetch every listed ID (via `get_email`) and run the per-email flow from step 4 on each before scanning for new unread mail. Delete the manifest only after every listed ID has been reconciled.

# Implementation Notes

- `search_emails` is for getting email IDs only — set maxResults to 500, paginate if needed
- `get_email` is for reading full email content — call this for EVERY email before categorizing
- Batch size for subagents: ~20 emails per subagent
- Entity names: kebab-case filenames, wikilink as full name
- All vault writes follow `@${CLAUDE_PLUGIN_ROOT}/references/ingestion-rules.md`
- Timestamps in local time (no UTC)
- Apply "Important" label via `modify_labels` — but ONLY after vault writes have been verified (see Crash-Safety Invariant)
- The per-email order is fixed: `get_email` → categorize → extract + write → `verify_vault.py --modified-only` → Gmail state mutation. Never reorder these steps. The reverse order silently drops signal on crash.
- `scratch/email-triage-in-flight.md` is managed directly via the Obsidian MCP (`vault_create`, `vault_read`, `vault_edit`, `vault_delete`). No helper script — `scratch/` is not covered by the immutability hook.
