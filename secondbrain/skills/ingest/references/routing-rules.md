# Ingest Routing Rules

> Routing table and write-operation tool mapping for the ingest skill.
> Shared write rules (entity templates, atomic sections, inbox processing)
> live in the top-level references — see pointers at the bottom of this file.

## Input → Destination Mapping

All paths below are vault-relative — the plugin reads/writes via Obsidian MCP, so no absolute path is needed.

| Content Type | Destination | Format |
|---|---|---|
| Task, action item, commitment | brain/status.md | Full inline metadata: [[entity]] #domain [due::] [energy::] [est::] |
| Idea, possibility, thought | scratch/ideas.md | Timestamped entry, #domain tags, [[entity]] links |
| Decision + rationale | brain/decisions.md | Atomic section heading, context, [[entity]] links |
| New person/company/place | entities/{name}.md | New entity file, typed frontmatter (type: person\|company\|organization\|place\|tool) |
| New term, acronym, shorthand | glossary.md | Added to lookup table with definition |
| Domain-specific info | {domain}/{domain}-index.md | New atomic section, [[internal links]] |
| Status update, blocker | brain/status.md | "Last Ingest" section with timestamp |
| Deadline, time-bound event | brain/deadlines.md | Entry with countdown + [[entity]] links |

## Write Operations

| Operation | Tool | Key Parameters |
|-----------|------|----------------|
| Append content under a heading | vault_patch | targetType: "heading", target: "Section Name", operation: "append" |
| Set/update frontmatter field | vault_patch | targetType: "frontmatter", target: "field-name", operation: "replace" |
| Create new entity file | vault_create | path: "entities/{name}.md", content: full file with frontmatter |
| Replace text in file | vault_edit | Find-and-replace pattern |
| Read file before writing | vault_read | Verify structure first |

## Shared Write Rules (pointers)

- **Entity Creation Template:** see `@${CLAUDE_PLUGIN_ROOT}/references/templates.md` (Entity File section).
- **Atomic Section Format:** see Rule 3 of `@${CLAUDE_PLUGIN_ROOT}/references/ingestion-rules.md`.
- **Inbox Processing:** see `@${CLAUDE_PLUGIN_ROOT}/references/templates.md` (Inbox Processing section).
