# Script Invocations

> Canonical invocation patterns for the deterministic Python scripts in
> `${CLAUDE_PLUGIN_ROOT}/scripts/`. Skills should reference this file
> rather than redocumenting the flags inline.
>
> All scripts assume `${VAULT_PATH}` resolves to the user's Obsidian vault
> root. On environments that do not set the variable, the common fallback
> is `${VAULT_PATH:-$HOME/vault}`.

---

## `verify_vault.py` — vault integrity check

Canonical: validate the vault and emit a JSON report of issues.

### Full scan (JSON)

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify_vault.py ${VAULT_PATH} --json
```

Use after dream-protocol consolidation, weekly-review, or any time a full
audit is needed. Output is JSON by default with `--json`; omit for
human-readable text.

### Modified-only scan (after a write)

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify_vault.py ${VAULT_PATH} --modified-only file1.md file2.md --json
```

Use in the **post-write validation** step of every skill that touches the
vault (ingest, session-end, weekly-review, email-triage, etc.). Pass the
list of files you just edited. This scopes the check to those files plus
their link targets, so it is fast enough to run after every write.

### Auto-fix pass

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify_vault.py ${VAULT_PATH} --fix
```

Fixes what can be fixed mechanically (duplicate headings, etc.). Use inside
dream-protocol's Phase 4 before the manual-fix loop, and inside `/init`'s
cleanup flow.

## `rebuild_manifest.py` — regenerate `_MANIFEST.md`

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/rebuild_manifest.py ${VAULT_PATH}
```

Walks the vault and rewrites `_MANIFEST.md` from current state. Run in
dream-protocol Phase 4 (after verification), at the end of weekly-review,
and during `/init`'s cleanup sequence. The script takes no flags.

## `archive_inbox.py` — move processed inbox files

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/archive_inbox.py ${VAULT_PATH}
```

Moves any inbox files marked `processed: true` (or `[processed:: true]`) to
`archive/inbox/`. Run this after completing ingest routing — **never** edit
or delete inbox files directly from a skill; always go through this script.

Dry run:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/archive_inbox.py ${VAULT_PATH} --dry-run
```

## `archive_contradiction.py` — soft-archive contradicted content

Dream-protocol Phase 3.12 uses this to move superseded vault content into
`archive/contradictions/YYYY-MM/` with a sidecar. MCP `vault_create` is
blocked for `archive/*` by the immutability hook, so this script is the
only sanctioned way to write contradiction archives.

The script NEVER modifies the original live file. The caller is responsible
for editing the live file in place after the archive + sidecar exist.

### Whole-file supersession

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/archive_contradiction.py ${VAULT_PATH} \
  --original-file ${VAULT_PATH}/brain/stale-note.md \
  --new-content-file /tmp/new-content.md \
  --source-description "2026-04-10 session log, from [[entities/alice]]" \
  --reasoning "Direct from the account owner supersedes the stale cached date" \
  --subject "acme-renewal-date"
```

### Section-anchor mode (smallest coherent unit)

When only part of a file is contradicted, pass the heading text of the
section to extract:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/archive_contradiction.py ${VAULT_PATH} \
  --original-file ${VAULT_PATH}/brain/status.md \
  --section-anchor "Acme Renewal" \
  --new-content-file /tmp/new-content.md \
  --source-description "..." --reasoning "..." --subject "acme-renewal"
```

The script extracts the heading plus everything up to the next heading of
the same or higher level. Heading match is case-insensitive.

### Dry run

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/archive_contradiction.py ${VAULT_PATH} \
  --original-file ${VAULT_PATH}/brain/status.md \
  --new-content-file /tmp/new-content.md \
  --source-description "..." --reasoning "..." --subject "..." \
  --dry-run
```

Validates inputs, computes the target paths and final slug, prints what
would be created, and writes nothing.

### Output

On success the script prints a single JSON line to stdout with the final
archive and sidecar paths (relative to the vault) and the resolved slug:

```json
{"archive_path": "archive/contradictions/2026-04/acme-renewal-date.md", "sidecar_path": "archive/contradictions/2026-04/acme-renewal-date.sidecar.md", "slug": "acme-renewal-date"}
```

Use this to embed a blockquote backlink in the live file:

```markdown
> Archived at [[archive/contradictions/2026-04/acme-renewal-date]]
```

The script handles slug collisions automatically by appending `-1`, `-2`,
... suffixes, and creates `archive/contradictions/YYYY-MM/` on demand.

## `create_entity_stubs.py` — create missing entity files

### By name

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/create_entity_stubs.py ${VAULT_PATH} kebab-name-1 kebab-name-2
```

Creates one stub per entity name using the standard entity frontmatter.
Names are in kebab-case and correspond to `entities/{name}.md`.

### From verify output

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify_vault.py ${VAULT_PATH} --json > /tmp/sb-verify.json
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/create_entity_stubs.py ${VAULT_PATH} --from-json /tmp/sb-verify.json
```

Reads missing-entity issues directly from a verify JSON report. Use this
in `/init`'s cleanup loop and any time dream-protocol needs to batch-create
entity stubs.

---

## Standard Post-Write Validation Block

Every skill that writes to the vault ends with the same block. Inline it
verbatim:

```bash
# 1. Verify modified files
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify_vault.py ${VAULT_PATH} --modified-only <files-you-touched> --json

# 2. If missing-entity errors, create stubs
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/create_entity_stubs.py ${VAULT_PATH} <entity-name>

# 3. Do NOT mark the operation complete until verification passes.
```
