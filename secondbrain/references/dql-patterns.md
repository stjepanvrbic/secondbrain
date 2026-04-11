# DQL Query Patterns

> Named, reusable DQL queries used across skills. Skills should reference a
> query by name (e.g., "run the `unprocessed-inbox` query") instead of
> re-deriving the text inline.
>
> Syntax reference lives in
> `@${CLAUDE_PLUGIN_ROOT}/references/vault-navigation.md`.

All queries are executed via `mcp__obsidian__dataview_query`.

---

## `unprocessed-inbox` — files awaiting ingest

```
TABLE file.name, file.ctime
FROM "inbox"
WHERE processed != true
SORT file.ctime ASC
```

Used by: dream-protocol (Phase 2), session-start sweep, ingest entry point.

## `stale-tasks` — no movement in >14 days

```
TASK
FROM "brain/status"
WHERE !done AND (date(today) - file.mtime) > dur(14 days)
```

Used by: dream-protocol (Phase 2), weekly-review. Stale = candidate for Someday
section or flagged review.

## `approaching-deadlines` — due within 7 days, not yet done

```
TASK
FROM "brain/status"
WHERE due AND (due - date(today)) <= dur(7 days) AND !done
```

Used by: dream-protocol (Phase 2 — auto-promotion input), deadline-check,
weekly-review. Results that are not already in "Urgent This Week" should be
promoted.

## `archive-candidates` — tasks done >7 days ago

```
TASK
FROM "brain/status"
WHERE done AND (date(today) - done) > dur(7 days)
```

Used by: dream-protocol (Phase 2 — archive pass), weekly-review. Results move
to `archive/completed-tasks-YYYY-MM.md`.

## `overdue-tasks` — past due, not yet done

```
TASK
FROM "brain/status"
WHERE due AND due < date(today) AND !done
```

Used by: deadline-check, weekly-review, morning-brief.

## `entities-to-verify` — wikilinks ingest flagged as uncertain

```
TABLE WITHOUT ID file.link AS "Source", L.text AS "Context"
FROM ""
FLATTEN file.lists AS L
WHERE L.verify = true
SORT file.mtime DESC
```

Finds every list item tagged `[verify:: true]` — ingest uses this marker
whenever it had to guess which entity a wikilink points at (see Rule 2a in
`@${CLAUDE_PLUGIN_ROOT}/references/ingestion-rules.md`). `FLATTEN
file.lists AS L` expands each page's bullets into their own rows so the
per-bullet inline field resolves — a page-level `WHERE verify = true`
would only match frontmatter or root-level fields and miss the marker
entirely. Each row returns the source file (`file.link`) and the literal
text of the flagged bullet (`L.text`). Used by: dream-protocol (Phase 2 —
gathered, Phase 3.5a — fuzzy-resolved or promoted to
`scratch/to-verify.md`). The flag stays in place until dream-protocol can
resolve it against a canonical entity, so the query is self-healing.

## `broken-links-and-orphans`

DQL cannot traverse link targets, so this is a script call rather than a
query. Use:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify_vault.py ${VAULT_PATH} --json
```

The JSON output includes `broken-wikilink` and `orphan` entries. See
`@${CLAUDE_PLUGIN_ROOT}/references/script-invocations.md` for full usage.

---

## Fallback Order When a Query Returns Empty

1. DQL query (preferred, structured)
2. `mcp__obsidian__vault_search` — full-text
3. `mcp__obsidian__vault_list` — path listing
4. `mcp__obsidian__vault_read` — direct file read when the path is known

If a tool errors, fall to the next. Do not retry the same failing tool.
