# Ingestion Rules

> Canonical rules for all vault writes. Loaded by every skill that writes to the vault.
>
> These rules are NON-NEGOTIABLE. Skill-specific routing (e.g., email-triage's
> per-category handling) lives in the skill file, not here.

---

## Rule 1 — Wikilink Enforcement (non-negotiable)

Every piece of information written to the vault MUST be linked to ALL related entities. **NO unlinked information enters the vault.** If text is written without wikilinks, go back and add them.

For every write, ask:

- **Who** is involved? → `[[entities/kebab-name|Display Name]]`
- **What domain?** → `[[domain-name]]`
- **What decision or goal does this relate to?** → `[[brain/decisions#section-heading]]`
- **What other sections does this cross-reference?** → `[[file#section-heading]]`

Link formats:

- Entity: `[[entities/kebab-name|Display Name]]`
- Section: `[[file#Section Heading]]`
- Domain: `[[domain-name]]`

Every task MUST link to at least one entity. Every bullet point SHOULD contain at least one `[[wikilink]]`.

## Rule 2 — Inline Metadata (for tasks)

Every task gets full inline metadata. **Field order is fixed:**

```markdown
- [ ] Task description [[entities/kebab-name|Display Name]] #domain [due:: YYYY-MM-DD] [energy:: low|medium|high] [est:: 5min|10min|15min|30min|1hr|2hr]
```

Order: **entity link → #domain → [due::] → [energy::] → [est::]**

Completed tasks carry a done stamp: `- [x] Task description [done:: YYYY-MM-DD]`

### Rule 2a — `[verify:: true]` for uncertain entity links

When a write has to guess at which entity a wikilink points to (ambiguous name, new person with no canonical entity file yet, typo that might collide with an existing entity), the writer MUST flag it with a structured inline marker on the same line as the wikilink:

```markdown
- Mentioned Jane from Acme re: renewal [[entities/jane-smith|Jane]] [verify:: true]
```

Why structured instead of a prose comment:

- Dream-protocol runs a nightly DQL query for `[verify:: true]` and either auto-resolves via fuzzy match once a canonical entity exists, or promotes the context to `scratch/to-verify.md` for human review.
- A prose `<!-- verify entity link -->` comment is invisible to DQL and will never be found.
- The flag stays in place until dream-protocol successfully resolves it; it is self-healing as the vault grows.

Field order when combined with task metadata: append `[verify:: true]` at the END of the line, AFTER `[est::]`. Do not insert it mid-sequence.

### Rule 2b — Blockquote backlinks for superseded content

When dream-protocol soft-archives contradicted content (Phase 3.12), the live file keeps a blockquote backlink pointing at the archive copy: `> Archived at [[archive/contradictions/YYYY-MM/<slug>]]`. The blockquote form makes it visually distinct from live content and searchable as a DQL pattern. Never rewrite or strip these backlinks — they are the recoverability path for the archived resolution.

## Rule 3 — Atomic Sections

Structure every write as an atomic section with a clear, searchable heading, 1-3 bullets, and wikilinks throughout. One concept per section. No sprawling prose dumps.

```markdown
## Section Heading — Descriptive Title

- Bullet one [[related-entity]]
- Bullet two [[another-entity]]
```

## Rule 4 — Entity Stubs on Reference

If a write mentions a new entity that does not yet have a file in `entities/`, create a stub. Use the entity template in `@${CLAUDE_PLUGIN_ROOT}/references/templates.md`, or run the helper:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/create_entity_stubs.py ${VAULT_PATH} kebab-name
```

Never leave a `[[wikilink]]` pointing at a file that does not exist. Create the stub as part of the same write.

## Rule 5 — No New Task Files

`brain/status.md` is the ONLY task file. Do not create `TASKS.md`, `todo.md`, or any other per-domain task file. Domain folders hold domain context; tasks live in `brain/status.md` tagged with `#domain`.

## Rule 6 — Immediate Flush

State changes during a session (new task, completed task, decision, blocker, deadline) go to the appropriate vault file **immediately**. Do not batch for session-end. Information that lives only in conversation is lost information.
