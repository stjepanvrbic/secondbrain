# Session-Start Architecture (developer documentation)

> This file is developer documentation for how the SessionStart hook and
> hot-memory flow behave. It is NOT loaded at runtime by the agent.
>
> As of T11, the SessionStart hook is `hooks/emit-hot-memory.sh`, a thin
> shell wrapper that resolves the active vault and delegates to
> `scripts/emit_hot_memory.py`. That script reads the pre-computed
> `brain/hot-memory.md` from the vault and emits it as the Claude Code
> `systemMessage`. Optionally, `scripts/vault_lookup_cwd.py` matches the
> current working directory to a vault entity and appends an
> `Active Project Context` section.
>
> The old `session-start` skill and its verbose bootstrap reference file
> have been retired: the rules they documented now live directly inside
> `brain/hot-memory.md`, which is regenerated nightly by `dream-protocol`
> and incrementally updated by the ingest subagent after each session.

---

## Components

### `hooks/emit-hot-memory.sh`

Fires on Claude Code's SessionStart events (`startup|clear|compact`).
Reads the JSON hook payload from stdin (may carry a `cwd`), resolves the
active vault from `~/.config/secondbrain/vaults.json`, and invokes
`scripts/emit_hot_memory.py` with `--vault` and (optionally) `--cwd`.

Contract:

- Always exits 0. A broken hook means a broken session.
- Always emits a single parseable JSON object on stdout, even on error.
- Never calls the MCP server. Filesystem access only, so it's fast
  (real-world <100ms target).
- When no vault is configured (pre-init), emits a "secondbrain not
  configured" fallback systemMessage.

### `scripts/emit_hot_memory.py`

The Python CLI that does the real work. Reads `brain/hot-memory.md`,
validates it via `hot_memory_schema.validate()`, and prints
`{"systemMessage": "..."}` to stdout.

Fallback JSON is emitted (and stderr logged) for every failure mode:

- Missing vault path → "secondbrain not configured"
- Missing `brain/hot-memory.md` → "hot memory is missing, run doctor"
- Invalid schema → "hot memory is invalid, run doctor"
- Any other OSError → generic "could not be loaded"

When `--cwd` is supplied and matches a vault entity,
`scripts/vault_lookup_cwd.py`'s `build_active_project_section` is called
to append an `Active Project Context` block.

### `scripts/vault_lookup_cwd.py`

Used both as a library (imported by `scripts/emit_hot_memory.py`) and
as a standalone CLI (`python3 scripts/vault_lookup_cwd.py --vault PATH
--cwd PATH`). Its job is to match the current working directory to an
entity in `<vault>/entities/` using a hybrid strategy:

1. Frontmatter match — entity file's YAML `paths:` list contains cwd
   (or an ancestor of it). Most specific declared path wins.
2. Fuzzy basename fallback — cwd basename matches entity filename
   case-insensitively (ignoring `.md`).
3. Frontmatter always beats fuzzy.

On a match, emits an `## Active Project Context` section with:

- The entity wikilink and cwd path.
- A one-line summary from the entity's frontmatter `summary:` field or
  first non-frontmatter body line.
- Open tasks from `brain/status.md` that reference the entity via
  `[[entities/<name>]]` wikilinks.
- Up to 3 recent `log.md` H2 entries mentioning the entity.

Missing peripheral files (`status.md`, `log.md`, `entities/`) are
tolerated — the matching sections are simply skipped.

---

## Lifecycle of a session

```
   ┌──────────────────────────────────────────────────────┐
   │                                                      │
   │  Claude Code session starts (or clear/compact)       │
   │                                                      │
   │  ├─► Fire SessionStart hook                          │
   │  │                                                   │
   │  │   hooks/emit-hot-memory.sh                        │
   │  │   │                                               │
   │  │   ├─ Read stdin (payload with optional cwd)       │
   │  │   ├─ Resolve active vault from vaults.json        │
   │  │   └─ Exec: python3 scripts/emit_hot_memory.py     │
   │  │                                                   │
   │  │       ├─ Read brain/hot-memory.md                 │
   │  │       ├─ Validate (hot_memory_schema.validate)    │
   │  │       ├─ Optionally append Active Project Context │
   │  │       │   (scripts/vault_lookup_cwd.py)           │
   │  │       └─ Emit `{"systemMessage": "..."}`          │
   │  │                                                   │
   │  ├─► Claude Code injects the systemMessage           │
   │  │                                                   │
   │  └─► Agent starts the turn with full context loaded  │
   │                                                      │
   └──────────────────────────────────────────────────────┘
```

Every writer of `brain/hot-memory.md` (`scripts/update_hot_memory.py`
in `--regenerate` and `--apply` modes) must produce a document that
passes `hot_memory_schema.validate()`. The hook hard-fails to a fallback
if validation fails — there is no lenient rendering mode.

---

## Why we retired the old `session-start` skill

In v3.3.x, the SessionStart hook injected a compact prompt telling the
agent to auto-invoke a `session-start` skill as its first action. That
skill then ran a bunch of Python helpers (`scripts/vault_guide.py`,
dynamic reads of `status.md`, `deadlines.md`, `me/profile.md`, etc.) and
loaded a verbose `references/session-start-bootstrap.md` reference file.

The round-trip was expensive (2–3 tool calls of wasted tokens every
session) and correctness suffered: if the agent ever forgot to invoke
the skill, the next turn operated on stale context.

T10 + T11 replaced that flow with a pre-computed `brain/hot-memory.md`
file that is:

- Regenerated nightly by the `dream-protocol` scheduled task
- Incrementally updated by the ingest subagent at Stop time
- Loaded deterministically by the hook on every session start

No agent cycles. No drift. The rules still live in the file, but they
are curated by the writers rather than re-read from a static reference
on every session.

---

## See also

- `scripts/hot_memory_schema.py` — schema + validator for `brain/hot-memory.md`
- `scripts/validate_hot_memory.py` — CLI wrapper around the validator
- `scripts/update_hot_memory.py` — THE ONLY WRITER of `brain/hot-memory.md`
- `scripts/connect_mcp_client.py` — Python HTTP wrapper for Connect MCP
  (used by the writer, not the reader)
