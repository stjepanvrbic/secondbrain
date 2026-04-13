# Architecture

> How `secondbrain` is designed, why those choices, and how it relates to existing patterns in the field.

This document is for users who want to understand the system before adopting it, and contributors who want to extend it. If you just want to use the plugin, the [README](README.md) is enough.

---

## The problem this solves

Long-running AI agents have a memory problem. Out of the box, an LLM has no persistent memory between sessions — every conversation starts blank. Tools like Claude Code's session compaction help inside a single conversation, but they don't survive a restart. Users who try to use AI as a "personal assistant" hit the same wall every day: re-explaining themselves, re-pasting context, re-deciding what's important.

The fix is **external memory** — a place outside the LLM's conversation that the agent reads at the start of each session and writes to as new information arrives. The hard part isn't the storage. It's the **discipline**: making sure information actually flows in (instead of dying in conversation), making sure it stays organized (instead of becoming a junk drawer), and making sure the agent reliably uses it (instead of answering from training data).

`secondbrain` is a Claude Code / Cowork plugin that gives the agent that external memory layer, backed by an Obsidian vault on disk.

---

## Three-layer model

The architecture follows a **three-layer pattern** that's been described in different forms by several people. The version this plugin most closely tracks is Andrej Karpathy's gist on [building a personal LLM wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) — credit there.

```
┌─────────────────────────────────────────────────────────┐
│  Layer 3: Schema (plugin-injected rules +               │
│            me/profile.md + glossary.md)                 │
│  ───────                                                 │
│  Static configuration. The agent's "personality" and    │
│  routing rules. Injected by the SessionStart hook as a  │
│  systemMessage; user-specific bio/rhythms live in       │
│  me/profile.md (user-curated). Glossary is also         │
│  user-curated. The agent almost never writes to this    │
│  layer.                                                  │
└─────────────────────────────────────────────────────────┘
                          ▲
                          │ governs
                          │
┌─────────────────────────────────────────────────────────┐
│  Layer 2: Wiki (brain/, entities/, log.md, etc.)        │
│  ───────                                                 │
│  The volatile, agent-maintained content. Tasks,         │
│  decisions, people, deadlines, timeline of operations.  │
│  Read AND written by the agent. The "memory."           │
└─────────────────────────────────────────────────────────┘
                          ▲
                          │ summarizes
                          │
┌─────────────────────────────────────────────────────────┐
│  Layer 1: Raw Sources (inbox/, optional sources/)       │
│  ───────                                                 │
│  Brain dumps, pasted articles, voice transcripts —      │
│  raw input the agent ingests but doesn't modify.        │
│  Always preserved (never deleted).                      │
└─────────────────────────────────────────────────────────┘
```

| Layer | Purpose | Who writes it | Examples |
|---|---|---|---|
| **Schema** | Configuration | Plugin-owned + human-curated | Plugin-injected routing rules (via the `SessionStart` hook emitting `brain/hot-memory.md` as a `systemMessage`); `me/profile.md`; `glossary.md` |
| **Wiki** | Agent-maintained memory | Agent (under schema rules) | `brain/`, `entities/`, `log.md`, `_MANIFEST.md` |
| **Raw Sources** | Unprocessed input | Human (via brain dumps) | `inbox/`, optional `sources/` |

The schema layer has two sub-parts:

- **Plugin-owned rules** — injected at every session start by the `SessionStart` hook (`hooks/emit-hot-memory.sh`), which reads the pre-computed `brain/hot-memory.md` from the active vault and emits it as a `systemMessage`. Hot memory is rebuilt nightly by `dream-protocol` and incrementally by the ingest subagent after each session. Developer documentation lives in `secondbrain/docs/session-start-architecture.md`.
- **User-curated profile** — `me/profile.md` holds the user's bio, daily rhythms, and preferences. Seeded by `/secondbrain:init` and refined organically through conversation. `glossary.md` (terms/acronyms/shorthand) is also user-curated.

Older plugin versions (v3.1.x–v3.3.2) shipped a `CLAUDE.md` template at the vault root. Since v3.3.3 that file is no longer scaffolded — its contents were split between the plugin-injected rules and `me/profile.md`. Any legacy `CLAUDE.md` sitting in a user's vault is orphaned but harmless; the plugin never touches it.

The principle is: **the human curates and directs. The LLM writes and maintains everything in the Wiki layer.** The human never edits Wiki content directly — they brain-dump into the Raw Sources layer, and the agent's `ingest` skill routes the dump into the right Wiki pages.

---

## Core operations

The plugin defines three core operations on the wiki layer, each implemented as one or more skills.

### Ingest

**Skill:** `/secondbrain:ingest`

When the user brain-dumps anything (text, paste, screenshot, voice), `ingest` parses the content and routes it to the appropriate Wiki pages. A single ingest typically touches multiple pages: the primary destination, any referenced entity pages, and the `log.md` audit trail. This is the "wiki maintenance" discipline — a brain dump about a meeting with a new person creates a task in `brain/status.md`, an entity page in `entities/`, a one-line entry in `log.md`, and possibly a decision entry in `brain/decisions.md`. After writing, a post-write validation hook runs `verify_vault.py` to ensure link integrity — the agent cannot proceed if validation fails.

### Query

**Skill:** `/secondbrain:knowledge-search`

When the user asks a question about their own context ("when is X due", "who is Y", "what did we decide about Z"), `knowledge-search` queries the wiki — DQL first via Obsidian Dataview, then graph links, then full-text search as a fallback. The agent answers with citations to specific files and sections, never from LLM memory. **The vault is the source of truth.**

### Lint

**Skills:** `/secondbrain:dream-protocol` (nightly), `/secondbrain:vault-review` (on-demand)

Lint operations are the wiki's "garbage collection." `dream-protocol` runs nightly at 2am and:
- Processes any remaining inbox items
- Promotes tasks approaching their deadline to URGENT
- Archives completed tasks older than 7 days
- Repairs broken wikilinks (fuzzy match + create missing entities)
- Deduplicates near-identical content
- Regenerates `_MANIFEST.md` (vault health metrics + content catalog)
- Appends a summary entry to `log.md`

`vault-review` is the on-demand version, triggered when the user wants a manual audit instead of waiting for nightly.

---

## Navigation files

Two files at the vault root act as navigation primitives.

### `_MANIFEST.md` — the index

Hybrid of two ideas: a structural index (what folders exist, what files are in each) and a content catalog (every wiki page with a one-line summary, organized by category). It also doubles as an operational dashboard (vault health metrics, active domains, recent activity from the last 7 days).

`_MANIFEST.md` is regenerated by `dream-protocol` on every nightly run. **Manual edits get overwritten** — all updates flow through the dream-protocol skill.

### `log.md` — the audit trail

Append-only chronological log. Every vault-modifying operation (ingest, dream-protocol, weekly-review, the Stop-hook ingest subagent, etc.) appends one entry. Format:

```markdown
## [YYYY-MM-DD HH:MM] <operation> | <title>
<one-to-three line body>
```

The consistent prefix makes history greppable with standard Unix tools:

```bash
grep "^## .* ingest " log.md       # all ingests
grep "^## \[2026-04" log.md        # everything in April 2026
tail -20 log.md                    # last 20 entries
```

Append-only means history is reconstructable forever. The `_MANIFEST.md` "Recent Activity" section is auto-built from the last 7 days of `log.md` entries.

---

## How this differs from Karpathy's gist

`secondbrain` borrows the three-layer model, the index/log navigation pattern, and the human-curates-LLM-maintains principle. But several intentional divergences:

| Karpathy's gist | `secondbrain` |
|---|---|
| Optimized for **knowledge bases** (articles, research, summaries) | Optimized for **life management** (tasks, deadlines, people, commitments) |
| `index.md` is a content catalog | `_MANIFEST.md` is content catalog + operational dashboard |
| `log.md` is the only audit trail | `log.md` is the audit trail; `brain/session-log.md` has richer per-session details |
| No scheduled task automation | Built-in scheduled tasks for morning briefing, deadline tracking, nightly maintenance, weekly review |
| One vault, one purpose | Vault is split into `brain/` (volatile state) + `entities/` (knowledge graph) + `me/` (self-knowledge) + topic folders |
| Optional Obsidian Web Clipper for source intake | Brain dump anywhere; the `ingest` skill handles routing |
| No default assumptions about the user | Comes with templates that expect you to fill in name, role, daily rhythm via `/init` |

The biggest divergence is **life-management vs knowledge-management orientation**. Karpathy's gist describes building a personal wiki about topics; `secondbrain` is built around running your day, your week, your projects, and your relationships. Both work — they emphasize different things.

Future versions may add an optional `sources/` layer (Karpathy-style raw curated documents) for users who want to use the plugin as a knowledge base alongside life management. Not in v3.5.x.

---

## Skill catalog

The plugin ships 13 skills. Most run automatically when a relevant trigger fires; you rarely invoke them by name. `init` and `doctor` are the only skills users typically invoke explicitly (via `/secondbrain:init` and `/secondbrain:doctor`).

| Skill | Purpose | Auto-invoked when |
|---|---|---|
| `init` | One-time setup wizard. Verifies prerequisites, scaffolds vault, installs scheduled tasks, seeds profile | User runs `/secondbrain:init` (explicit) |
| `doctor` | Two-turn diagnose-then-treat diagnostic. First turn reports pass/fail with fix commands; second turn applies the approved fixes | User runs `/secondbrain:doctor` (explicit) |
| `ingest` | Routes brain dumps to vault with mandatory wikilinks. Also dispatched by the Stop hook via the `secondbrain-ingester` subagent to extract facts from each turn | User pastes text, says "brain dump", or Stop hook fires |
| `knowledge-search` | Vault-backed query with citations | User asks about their own context (people, dates, decisions, status) |
| `whats-next` | Picks ONE next task, energy-matched, no options. Reads the cached `brain/morning-brief.md` when available | User asks "what's next" or starts a session without a task |
| `email-triage` | Full agent reading of every email, extracts action items, archives noise | Scheduled task or user asks to check email (requires Gmail MCP) |
| `morning-brief` | Process overnight inbox + build today's energy-matched plan. Dispatched by the `secondbrain-morning-brief` subagent on its scheduled cron | Scheduled at user's wakeup time |
| `deadline-check` | Lightweight midday scan, auto-promote urgent tasks | Scheduled at 1pm |
| `end-of-day` | Review day vs plan, prompt for brain dump, flush state | Scheduled at evening shutdown time |
| `weekly-review` | Full Sunday audit, build next week's plan | Scheduled Sunday 8pm |
| `dream-protocol` | Nightly vault maintenance — lint, consolidate, rebuild manifest, regenerate `brain/hot-memory.md` | Scheduled 2am (nightly) or invoked by `init` for first-time setup |
| `vault-review` | On-demand vault audit (focused deadline review or full weekly audit) | User asks "how am I doing?", "what's overdue?", "audit my tasks", etc. |
| `undo-last-turn` | Git-based recovery for "the last commit was wrong". Resets the vault to the previous Stop-hook commit | User asks to undo the last turn |

Everything except `init` and `doctor` runs automatically based on hooks, schedules, and conversational triggers — the routing rules are injected by the `SessionStart` hook (via the nightly-built `brain/hot-memory.md`). Developer documentation lives in `secondbrain/docs/session-start-architecture.md`.

---

## Bundled scheduled tasks

`init` installs the bundled scheduled tasks from `secondbrain/scheduled-tasks/MANIFEST.md` by default (user can opt in/out per task during setup):

| Task | Default cron | Skill |
|---|---|---|
| morning-brief | `0 8 * * *` (8:00am daily) | `secondbrain-morning-brief` (subagent) |
| morning-briefing | `30 10 * * *` (10:30am daily) | `morning-brief` |
| deadline-tracker | `0 13 * * *` (1pm daily) | `deadline-check` |
| email-triage | `0 9 * * 1-5` (9am weekdays) | `email-triage` (requires Gmail MCP) |
| end-of-day-capture | `30 19 * * *` (7:30pm daily) | `end-of-day` |
| weekly-review | `0 20 * * 0` (Sunday 8pm) | `weekly-review` |
| dream-protocol | `0 2 * * *` (2am daily) | `dream-protocol` |

In Claude Code, `init` calls `CronCreate` directly to register these. In Cowork (which doesn't expose `CronCreate` to plugins), `init` instead prints copy-pasteable `/schedule` commands the user runs in the Cowork chat.

---

## Why Obsidian + Dataview

The wiki layer is a directory of Markdown files. The agent reads/writes them via the [Connect MCP plugin](https://github.com/joch/obsidian-connect-mcp). The plugin's `.mcp.json` configures the connection.

[Dataview](https://blacksmithgu.github.io/obsidian-dataview/) is required because the plugin uses inline metadata (`[due:: 2026-04-15]`, `[energy:: high]`, `[est:: 30min]`) on tasks, which Dataview indexes for queryability. The `knowledge-search` skill can run DQL queries like "all tasks due in the next 7 days" or "all entities tagged with #immigration."

You don't need to be an Obsidian power user. The plugin generates the structure for you and the agent maintains it. You'll mostly interact with the system through Claude Code / Cowork, not by editing files in Obsidian. But the vault is human-readable Markdown — if Claude Code disappears tomorrow, your data is still yours, sitting in plain files.

---

## Privacy

The plugin runs **entirely on your machine**. No data leaves your laptop except:
- LLM API calls to Anthropic (the same calls you make in any Claude Code / Cowork session)
- Whatever your Obsidian sync configuration does (Obsidian Sync, iCloud, Google Drive, Syncthing — see [SYNC.md](SYNC.md))

The plugin itself sends nothing. There's no telemetry, no analytics, no remote calls. The vault is your data, on your filesystem, encrypted at rest by your OS.

The one ambient leak vector is Anthropic's LLM API: every time the agent reads a vault file, the contents are part of the prompt sent to the model. If you're storing things you don't want Anthropic to ever see, don't put them in the vault.

---

## Credits

- [Andrej Karpathy's gist on personal LLM wikis](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) — three-layer model, index/log navigation pattern, "human curates, LLM maintains" principle
- [Obsidian](https://obsidian.md) and the [Connect MCP plugin](https://github.com/joch/obsidian-connect-mcp) — vault storage and MCP bridge
- [Dataview](https://blacksmithgu.github.io/obsidian-dataview/) — DQL query layer
- The Claude Code and Claude Cowork teams at Anthropic for the plugin and skill systems
