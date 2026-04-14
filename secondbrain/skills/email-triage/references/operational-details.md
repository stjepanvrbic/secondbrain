# Email Triage Operational Details

Use this only when you need the exact crash-recovery template, manifest format,
or expanded routing notes. The main skill body is the default contract.

## In-Flight Manifest Template

Filename pattern:

`scratch/email-triage-in-flight-{batch-id}.md`

`batch-id` format:

- `YYYY-MM-DDTHH-MM-SS-{nnnn}`
- use local start time
- replace colons with hyphens
- append a 4-character lowercase alphanumeric suffix so parallel workers do not
  collide

Body template:

```markdown
---
created: <ISO timestamp>
batch_id: <YYYY-MM-DDTHH-MM-SS-nnnn>
---

# Email Triage In-Flight

- <gmail-message-id-1>
- <gmail-message-id-2>
```

Lifecycle:

1. Create before the first email is processed.
2. Trim after each email is fully complete.
3. Delete when no IDs remain.

## Routing Notes

- `URGENT` / `IMPORTANT`
  - ingest all durable signal
  - tasks -> `brain/status.md`
  - deadlines -> `brain/deadlines.md`
  - decisions -> `brain/decisions.md`
  - new entities -> `entities/{name}.md`
  - update existing entities when the email adds facts worth preserving
- `INFORMATIONAL`
  - ingest only signal worth referencing later
  - otherwise no vault write
- `NOISE`
  - no vault write
  - archive only after the email has actually been read

## Gmail Mutation Order

Never mutate Gmail before the vault is safe.

Order:

1. fetch and read the email
2. write any needed vault signal
3. run `verify_vault.py --modified-only`
4. only then mark read / label / archive

## Response Budget

Scheduled dispatch reporting should stay terse:

- status line
- action-item count
- important-label count
- archive count
- at most 1 urgent signal
