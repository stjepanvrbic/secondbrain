---
name: email-triage
description: >
  Use when the user asks to check email, triage inbox, or scan unread mail,
  and when the scheduled morning/midday/evening email triage task runs.
  Fully read every unread email, extract durable signal to the vault, label
  important mail, archive noise, and finish at zero unread.
metadata:
  version: "3.5.21"
---

# Core Rule

Triage all unread mail to **zero unread**. Every email must be individually
fetched with `get_email` and understood by an agent before it is categorized.
No keyword-only filtering, no sender heuristics, no categorizing from search
result snippets.

This skill is part of Cowork scheduled dispatch. Keep the final response short.

# Forbidden Actions

- Deleting emails.
- Categorizing from search result summaries instead of `get_email`.
- Skipping an unread email for any reason.
- Dumping full email bodies back to the user.
- Finishing with unread emails remaining.
- Marking an email read before its vault writes are verified.

# Required Reads

1. Read `_MANIFEST.md`.
2. Read `@${CLAUDE_PLUGIN_ROOT}/references/vault-navigation.md`.
3. Read `@${CLAUDE_PLUGIN_ROOT}/references/ingestion-rules.md`.
4. Read `@${CLAUDE_PLUGIN_ROOT}/references/script-invocations.md`.
5. If you detect orphan manifests, need the exact manifest template, or need the
   full category-routing notes, read
   `@${CLAUDE_PLUGIN_ROOT}/skills/email-triage/references/operational-details.md`.

# Non-Negotiable Safety Contract

**No Gmail state mutation** may happen for an email until every vault write
derived from that email is complete and validated with
`verify_vault.py --modified-only`.

That means:

1. Read email.
2. Write signal to vault.
3. Verify the touched files.
4. Only then mutate Gmail state.

The irreversible failure is "marked read but not written." Avoid that at all
costs.

# In-Flight Manifest

Each batch worker owns one `scratch/email-triage-in-flight-{batch-id}.md`
sidecar. This **In-Flight Manifest** is the crash-recovery anchor.

Rules:

- Write the manifest before the worker touches its first email.
- The manifest lists only that worker's current email IDs.
- After an email is fully complete, remove its ID from the manifest.
- Delete the manifest when the batch drains cleanly.
- At successful skill exit, no `email-triage-in-flight-*.md` files may remain.

If orphan manifests exist at start, reconcile them before scanning fresh unread
mail.

# Triage Algorithm

1. Recover any orphan `email-triage-in-flight-*.md` files from `scratch/`.
   Re-fetch every listed email and run the normal per-email flow before moving
   on.
2. Search for unread email IDs. Search is only for collecting IDs, not for
   categorization.
3. Batch IDs into groups of about 20 and process batches in parallel workers.
   Each worker uses this same skill contract.
4. For each email ID:
   - fetch full content with `get_email`
   - read the whole email
   - categorize as `URGENT`, `IMPORTANT`, `INFORMATIONAL`, or `NOISE`
   - ingest durable signal into the vault when the email contains tasks,
     deadlines, decisions, entities, or facts worth keeping
   - run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify_vault.py ${VAULT_PATH} --modified-only <touched-files> --json`
   - if verification fails, fix the vault first and re-run verification
   - only after verification is clean: label/archive/mark-read as appropriate
   - remove the finished ID from that worker's manifest
5. Re-run unread search.
6. Do not finish until unread count is zero and no in-flight manifests remain.

# Routing Contract

- `URGENT` and `IMPORTANT`: fully ingest durable signal.
- `INFORMATIONAL`: ingest only what is worth preserving; otherwise mark read.
- `NOISE`: no vault write, archive after reading it.

When ingesting, use the shared rules from `ingestion-rules.md`:

- tasks and action items -> `brain/status.md`
- deadlines -> `brain/deadlines.md`
- decisions and agreements -> `brain/decisions.md`
- new or updated contacts/entities -> `entities/{name}.md`
- keep wikilinks, metadata field order, and atomic sections consistent

Priority senders still matter: immigration/legal, active stakeholders, family,
and time-sensitive financial contacts get immediate attention.

# Crash Recovery and Duplicates

- Duplicate action items must be absorbed idempotently by checking existing
  status entries before adding a new one.
- If verification fails mid-email and you cannot repair it, leave the email
  unread and keep its ID in the manifest so the next run retries it.
- If a prior worker crashed, recover its orphan manifest first. One crashed
  worker must not block recovery of the others.

# Triage Windows

- Morning: full unread sweep.
- Midday: catch-up sweep since morning.
- Evening: same process, but bias non-urgent work toward tomorrow.

All windows still end at zero unread.

# Response Style

Brief summary only. Keep the final response to 5 short lines or fewer:

```text
Email triage done — 0 unread remaining.
Action items: 3 added to status.
Labeled important: 7 emails.
Archived: 12 emails.
Urgent: Follow up with [[entities/mmh]] on LCA status.
```

At most 1 urgent signal. No long lists. No narrative recap.
