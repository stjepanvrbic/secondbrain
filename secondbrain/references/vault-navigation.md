# Vault Navigation Reference

> This vault is a **zettelkasten knowledge graph** managed via Obsidian with Dataview.
> Every skill MUST read `_MANIFEST.md` first to understand current vault state.

## DQL-First Navigation (MANDATORY)

**You MUST use DQL queries via `mcp__obsidian__dataview_query` as the primary tool to navigate this vault.** The vault is a knowledge graph — query it, don't grep it. DQL gives you structured, filtered, sorted results across the entire graph.

Read individual files only when you need full context that a query cannot provide (e.g., reading a complete entity profile, understanding a decision's rationale, or loading a file's full content for editing).

**Use `mcp__obsidian__graph_links` to explore relationships** — find all files linking to an entity, discover connections between domains, trace dependency chains.

## Zettelkasten Principles

- **Atomic notes**: one concept per section/file
- **Everything links**: every piece of content references related entities, files, and sections via [[wikilinks]] — see `@${CLAUDE_PLUGIN_ROOT}/references/ingestion-rules.md` for the canonical wikilink rules and link formats

## Vault Structure

| Path | Contents |
|------|----------|
| `_MANIFEST.md` | **Master index — READ THIS FIRST.** Vault health, domain list, content catalog, file tree, recent activity |
| `log.md` | **Append-only chronological log** — every operation (ingest, dream-protocol, session-end, etc.) appends an entry. Greppable history. See "log.md format" below |
| `brain/status.md` | Current focus, active tasks (SINGLE SOURCE OF TRUTH), blockers, last session summary |
| `brain/commitments.md` | DEPRECATED — archived. Replaced by brain/status.md |
| `brain/deadlines.md` | Hard dates and countdowns |
| `brain/goals.md` | Life goals and priorities |
| `brain/decisions.md` | Decisions + rationale + context links |
| `brain/session-log.md` | Reverse-chronological session history (richer than log.md — full session details) |
| `entities/{kebab-name}.md` | Person/company/place/tool profiles (frontmatter: type, domains, relationship) |
| `entities/directory.md` | Quick-reference entity lookup table |
| `inbox/*.md` | **IMMUTABLE via MCP.** Raw input staging. Agent CANNOT write/patch/delete. Moved to archive/inbox/ by scripts/archive_inbox.py after ingest processing. |
| `{domain}/` | Domain folders the user creates as life areas grow |
| `me/` | Self-knowledge: profile.md, energy.md, adhd-protocol.md (optional) |
| `archive/` | **IMMUTABLE via MCP.** Historical raw data. Agent CANNOT write/patch/delete. Only modified by sanctioned scripts (archive_inbox.py, migrate_v2_to_v3.py). |
| `scratch/` | Ideas, unsorted notes |
| `glossary.md` | Terms, acronyms, shorthand |

## log.md Format (Karpathy-style)

`log.md` lives at the vault root and is **append-only**. Every vault-modifying operation appends one entry. Format:

```markdown
## [YYYY-MM-DD HH:MM] <operation> | <title>
<one-to-three line body>
```

**Operations:** `ingest`, `session-start`, `session-end`, `dream-protocol`, `weekly-review`, `deadline-check`, `morning-brief`, `end-of-day`, `email-triage`, `init`, `manual`

**Skills that MUST append entries:**
- `ingest` — for every brain dump processed
- `dream-protocol` — for the nightly run summary
- `session-end` — for the session summary
- `email-triage` — for each scheduled triage run
- `weekly-review` — for the weekly audit

**Why this format:** consistent prefixes make it greppable with standard Unix tools. `grep "^## .* ingest " log.md` lists every ingest. `grep "^## \[2026-04" log.md` lists everything in April. The append-only discipline means history is reconstructable forever.

**Reading:** the most recent entries are at the bottom of the file. Use `tail` or read the last 20-50 lines for "what happened recently." `_MANIFEST.md`'s "Recent Activity" section is auto-built from the last 7 days of `log.md` entries by dream-protocol.

## Inline Fields (Dataview-queryable)

| Field | Type | Valid Values | Found In |
|-------|------|-------------|----------|
| `[due:: DATE]` | Date | `2026-03-28` (ISO) | status.md, deadlines.md |
| `[energy:: LEVEL]` | String | `low`, `medium`, `high` | status.md |
| `[est:: TIME]` | String | `5min`, `10min`, `15min`, `30min`, `1hr`, `2hr` | status.md |
| `[blocked-by:: LINK]` | Link | `[[entities/name]]` | status.md |
| `[done:: DATE]` | Date | `2026-03-20` (ISO) | status.md |
| `type` | Frontmatter | `person`, `company`, `organization`, `place`, `tool` | entities/ |
| `domains` | Frontmatter | `[domain1, domain2]` | entities/ |

## DQL Syntax Reference

```
TABLE field1, field2 FROM "path" WHERE condition SORT field ASC|DESC LIMIT n
LIST FROM "path" WHERE condition
TASK FROM "path" WHERE condition
```

**Date math:** `date(today)`, `date(today) + dur(7 days)`, `date(today) - dur(14 days)`
**Text search:** `contains(text, "term")`, `contains(file.name, "term")`
**File metadata:** `file.mtime`, `file.name`, `file.path`, `file.outlinks`
**Null/existence:** `WHERE field` (exists), `WHERE !field` (missing)
**Link scope:** `FROM [[entities/name]]` (all files linking to entity)

### Common Mistakes
- `TASK` queries do NOT support TABLE-style column lists — use `TASK FROM "path" WHERE condition`
- `FROM` paths use forward slashes and double-escaped quotes in tool calls: `"brain/status"`
- `WHERE !completed` filters open tasks; `WHERE completed` filters done tasks
- Date comparisons: `due <= date(today) + dur(7 days)` NOT `due <= today + 7`

## MCP Tools

### Read/Query Tools
| Tool | When to Use |
|------|-------------|
| `mcp__obsidian__dataview_query` | **PRIMARY** — structured data queries, filtered/sorted results |
| `mcp__obsidian__graph_links` | Explore relationships — incoming/outgoing links for a note |
| `mcp__obsidian__vault_search` | Full-text keyword search when DQL can't express what you need |
| `mcp__obsidian__vault_read` | Read a specific file by path (for full context) |
| `mcp__obsidian__vault_list` | List files matching a glob pattern |
| `mcp__obsidian__graph_info` | Get file-level metadata |

### Write Tools
| Tool | When to Use |
|------|-------------|
| `mcp__obsidian__vault_patch` | **PREFERRED** — edit by heading/block/frontmatter, surgical section edits |
| `mcp__obsidian__vault_edit` | Find-and-replace text within a file |
| `mcp__obsidian__vault_create` | Create new files (entities, archive entries) |
| `mcp__obsidian__vault_update` | Overwrite entire file (use sparingly) |
| `mcp__obsidian__vault_delete` | Soft-delete a note (moves to trash) |
| `mcp__obsidian__vault_edit_line` | Insert or replace at a specific line number |

### Tool Priority (Fallback Order)
1. **DQL query** → structured, fastest, preferred
2. **graph_links** → relationship exploration
3. **vault_search** → full-text when DQL can't express what you need, or you simply need to read a file to understand more
4. **vault_read** → direct file read when you know exact path
5. **vault_list + vault_read** → discover then read
6. **Direct file system reads** → ONLY if Obsidian MCP is unreachable

If a tool returns an error, try the next in priority. Do not retry the same failing tool.

The MCP server is provided by the Connect MCP plugin (`connect-mcp`). Obsidian must be running for these tools to work. The init script launches Obsidian automatically if needed.

## Scripts

Deterministic operations are handled by Python scripts in `scripts/`. Canonical invocation patterns (flags, usage in skill flows) are defined in `@${CLAUDE_PLUGIN_ROOT}/references/script-invocations.md`. Name-only index for discoverability:

- `verify_vault.py` — vault integrity check (broken wikilinks, missing entities, duplicate headings)
- `create_entity_stubs.py` — create missing entity files from names or verify JSON
- `archive_inbox.py` — move processed inbox files to `archive/inbox/`
- `rebuild_manifest.py` — regenerate `_MANIFEST.md` from current vault state
- `vault_guide.py` — dynamic vault context summary (active domains, recent activity, current focus); run at session-start

## See Also

- `@${CLAUDE_PLUGIN_ROOT}/references/environments.md` — Claude Code vs Cowork paths, scheduled tasks, MCP config, platform differences
