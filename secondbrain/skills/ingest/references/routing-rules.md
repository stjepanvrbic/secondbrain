# Ingest Routing Rules

> Detailed routing table, entity creation template, and inbox processing rules.

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

## Entity Creation Template

When ingestion mentions a new entity not in `entities/`, create `entities/{kebab-name}.md`:

```yaml
---
type: person | company | organization | place | tool
domains: [domain1, domain2]
relationship: [how the user relates to this entity]
created: [TODAY]
updated: [TODAY]
---

# [Entity Full Name]

## Overview
[1-2 sentence description]

## Domains Involved
[[domain1]], [[domain2]]

## Contact Info (if person/company)
- Email: [or "not stored"]
- Phone: [or "not stored"]
- Location: [if place]

## Relationships
- [[related-entity1]]
- [[related-entity2]]
```

## Inbox Processing

When session-start detects unprocessed files in `inbox/`:

```
FOR each file in inbox/:
  1. Read file contents
  2. Run through normal ingest routing
  3. Add to vault (status.md/ideas/decisions/entities as appropriate)
  4. After ALL items are routed, run:
     python3 ${CLAUDE_PLUGIN_ROOT}/scripts/archive_inbox.py ${VAULT_PATH}
     This moves processed inbox files to archive/inbox/
  5. NEVER modify the original inbox file in place
  6. NEVER delete inbox files — they are archived, not removed
ENDFOR
```

## Atomic Section Format

When writing to existing files, structure as atomic sections:

```markdown
## Section Heading — Descriptive Title

Content here [[with]] [[wikilinks]].

- Bullet point [[related-entity]]
- Another bullet [[another-entity]]
```

Each section has:
- Clear heading (descriptive, searchable)
- Content (1-3 bullets or short paragraphs)
- Wikilinks throughout
