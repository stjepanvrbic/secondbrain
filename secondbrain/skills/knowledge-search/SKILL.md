---
name: knowledge-search
description: >
  This skill should be used when the user asks "when is...", "what's the status of...",
  "who is...", "what did we decide about...", "search my notes", "check my vault",
  "do I have anything on...", or any question about their own plans, people,
  decisions, or timeline. Vault is source of truth — FORBIDDEN to answer from
  memory when vault has the answer.
metadata:
  version: "3.5.14"
---

# Core Rule

**Vault is the source of truth, not training data.** When the user asks about their own life, plans, people, or decisions, ALWAYS search the vault first. Never guess the answer, or try to infer from conversation history as it might be stale.

# Prerequisites
1. Read `_MANIFEST.md` for current vault state.
2. For vault navigation, read `@${CLAUDE_PLUGIN_ROOT}/references/vault-navigation.md`.
3. For named DQL query patterns, read `@${CLAUDE_PLUGIN_ROOT}/references/dql-patterns.md`.

# Search Strategy

Find the answer — construct queries appropriate to the question type.

Use DQL queries to find information whenever possible, and read files only if you need additional context or initial context. The vault uses wikilinks to construct a knowledge graph that can be queried directly. Named queries for common cases (`unprocessed-inbox`, `stale-tasks`, `approaching-deadlines`, `overdue-tasks`, `archive-candidates`) are defined in `@${CLAUDE_PLUGIN_ROOT}/references/dql-patterns.md` — reference them by name instead of rewriting the query text.

| Question Type | Data Needed | Where to Look |
|---------------|-------------|---------------|
| Person/entity ("who is X") | Canonical entity file + backlinks | exact entity -> aliases/normalized variants -> parent expansion -> graph_links or DQL |
| Timeline ("when is X due") | Tasks/deadlines with due dates | brain/status.md, brain/deadlines.md |
| Status ("what's the status of X") | Recent activity | brain/status.md, {domain}/index.md |
| Domain ("what's going on with X") | Overview + tasks | {domain}/index.md, status.md |
| Decision ("what did we decide about X") | Decision record | brain/decisions.md, domain files |
| Task ("what tasks do I have for X") | Filtered task list | brain/status.md |
| General ("search my notes for X") | Full-text match | vault_search, then read matches |

## Fallback Order
1. DQL query / vault_search / graph_links
2. Person/entity -> canonical entity lookup (exact/normalized/alias/parent) + graph_links for connections
3. Timeline/deadline -> DQL with date filter on status.md, deadlines.md
4. Status -> vault_read status.md directly
5. Relationship -> graph_links from entity
6. General/free-text -> vault_search, then read top results

# Response Style

## Direct Answer, Cite Sources

**Good:**
```
Alex's birthday is April 12. She's based in Boston currently. See [[entities/alex-rivera]].
```

**Good:**
```
Status on the contract review: draft sent Mar 16, waiting on approval from [[entities/acme-legal]].
Latest update in [[brain/status#contract-context]].
```

**Poor (too verbose):**
```
Let me search your vault... I found several references...
```

**Poor (no source):**
```
Alex's birthday is April 12.
[No citation]
```

## Citation Format
- Brief inline citations at end in [[wikilinks]]
- Only cite relevant sources (2-3 max)

## Flag Stale Information
```
The vault says X (last updated MM-DD). This might be stale — check if it's still current.
```

## When Not Found
```
I don't have anything on that in your vault.
```

# Search Implementation Details

**Timeline queries:** Search brain/deadlines.md -> brain/status.md [due::] -> domain folder -> brain/goals.md milestones.

**Status queries:** Search brain/status.md -> domain/index.md -> extract urgency/blockers/progress.

**Person/entity queries:** Resolve the canonical entity first (exact slug, normalized name, explicit alias, then parent expansion when relevant), then load the canonical file and backlinks.

**Decision queries:** Search brain/decisions.md -> domain files -> status.md for resulting tasks.

**Domain queries:** Load {domain}/index.md -> status.md for domain tasks and blockers.

**Keyword search (fallback):** Grep all .md files, return top 3-5 matches with context snippets as [[wikilinks]].

# Error Handling

- **File not found**: "That file doesn't exist in your vault yet."
- **Broken wikilink**: Add citation anyway, note "link may be broken"
- **Ambiguous question**: Ask ONE clarifying question max
- **No answer in vault**: "I don't have anything on that in your vault."
- **Vault unreachable**: "Vault is inaccessible, I can't search right now."

# Forbidden Actions

- Answering from general knowledge when vault likely has the answer
- Providing multiple options for what user might have meant (ask once max, or guess)
- Withholding sources/citations
- Long summaries (answer + cite, done)
- Saying "Let me search" or "I found..." (just answer)

# Implementation Notes

- Search is CASE-INSENSITIVE
- "Within 7 days" = next 7 calendar days
- "Stale" = last updated >14 days ago
- If an entity has aliases or a narrower child form, resolve to the canonical entity first and expand to the parent entity when the child explicitly points there
