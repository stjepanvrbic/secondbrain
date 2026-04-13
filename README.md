# secondbrain

> A persistent, scheduled second brain for Claude Code and Claude Cowork — backed by your own Obsidian vault.

You install the plugin, run `/secondbrain:init`, and from that moment on Claude remembers you. Tasks, deadlines, decisions, the people in your life, what you said yesterday. It runs on autopilot — morning briefing at your wakeup time, deadline checks at 1pm, vault maintenance at 2am, weekly review on Sundays. You don't manage the system. The system runs your day and gets out of the way.

This is not "ChatGPT with extra steps." It's an opinionated **memory layer** with a strong point of view: the agent writes everything into a vault you own (plain Markdown, not a SaaS database), reads it at every session start, and never lets information die in conversation. Vault integrity is enforced programmatically — Python scripts handle deterministic work (validation, manifest rebuilds, entity stubs), hooks block the agent if writes create integrity issues, and the agent focuses on judgment calls.

---

## Who this is for

You want a persistent assistant that **remembers you**, runs your daily routines, and gets smarter the longer you use it. Setup is automated — the init script installs Obsidian, configures plugins, wires up the MCP connection, and scaffolds your vault. You answer 2-3 profile questions and you're done.

You're fine with installing Obsidian (free) as the storage layer. You don't need to be an Obsidian power user — the plugin scaffolds everything for you and the agent maintains it. You'll mostly interact with the system through Claude Code or Cowork, not by editing files in Obsidian. But your data is human-readable Markdown sitting in plain files on your laptop. If Claude Code disappears tomorrow, your data is still yours.

Especially good if you have ADHD, are juggling many projects at once, or just hate context switching.

---

## Quick start — Claude Code

```
/plugin marketplace add stjepanvrbic/secondbrain
/plugin install secondbrain@stjepanvrbic-secondbrain
/secondbrain:init
```

For local development or testing from a clone:
```bash
claude --plugin-dir /path/to/secondbrain
```

Then follow the prompts. The init skill automates nearly everything:
- Installs Obsidian if missing (`brew`, `snap`, or `winget` depending on platform)
- Downloads and installs the Dataview and Connect MCP plugins from GitHub
- Reads the API key and port from plugin config
- Writes environment variables to your shell config (zsh/bash/fish/PowerShell)
- Scaffolds your vault with starter files
- Installs the bundled scheduled tasks from `secondbrain/scheduled-tasks/MANIFEST.md`
- Asks 2-3 profile questions (name, work, communication style)
- Runs verification to confirm everything works

About 5 minutes. Works on macOS, Linux, and Windows.

## Quick start — Claude Cowork

1. Download the latest `secondbrain-vX.Y.Z.zip` from the [GitHub Releases page](https://github.com/stjepanvrbic/secondbrain/releases/latest)
2. Open Claude Desktop, switch to the **Cowork** tab
3. Click **Customize** → **Browse plugins** → **Upload** → select the ZIP
4. Click **Install** → **Authorize**
5. In a new Cowork chat, run: `/secondbrain:init`
6. After init prints a list of `/schedule` commands, copy each one into the Cowork chat to enable scheduled tasks (Cowork doesn't let plugins create scheduled tasks directly — this manual step is unavoidable for now)

The init skill works the same in Cowork as in Code, except it auto-detects the environment and adapts (uses Cowork's `/schedule` skill instead of Code's `CronCreate` for scheduled tasks).

---

## Prerequisites

- **Claude Code** OR **Claude Cowork** (Claude Desktop with Cowork enabled)
- **Python 3.8+** (for the script suite — zero external dependencies)
- **Obsidian** (free) — https://obsidian.md (init installs it automatically if missing)
- ~5 minutes for initial setup

---

## What `/secondbrain:init` does for you

Init is mostly automated via `scripts/init_obsidian.py`. You don't need to know what an env var, MCP, or shell config is — the script handles it.

1. **Detect platform** — macOS, Linux, or Windows (including WSL)
2. **Install Obsidian** — via `brew` (macOS), `snap` (Linux), or `winget` (Windows), skipped if already installed
3. **Detect or create vault** — finds existing Obsidian vaults, or creates `~/secondbrain-vault`
4. **Scaffold vault structure** — creates `brain/`, `entities/`, `me/`, `inbox/`, `archive/`, `scratch/` and all critical files
5. **Install plugins** — downloads Dataview and Connect MCP from GitHub releases into `.obsidian/plugins/`
6. **Configure MCP connection** — reads API key and port from plugin config, writes env vars to your shell config
7. **Verify** — runs `verify_vault.py` and `rebuild_manifest.py` to confirm everything is valid

After the automated setup, the init skill asks 2-3 profile questions (name, work, communication style) and installs the bundled scheduled tasks from `secondbrain/scheduled-tasks/MANIFEST.md`.

**Idempotent:** running `/secondbrain:init` twice is safe. Re-runs detect what's already done and only complete missing pieces.

**Verify mode:** `/secondbrain:init --verify` delegates to `scripts/verify_vault.py` — runs all health checks with no side effects.

---

## First-session checklist

After `/secondbrain:init` finishes, try these:

```
what's next?              # get your first task — energy-matched, just one
brain dump: <anything>    # ingest something into the vault
who am I                  # test knowledge search against your seeded profile
```

The hooks fire automatically: the `SessionStart` hook injects pre-computed hot memory (`brain/hot-memory.md`) as a `systemMessage`, so the agent has your context loaded before you even finish typing. After every turn the `Stop` hook commits your vault and dispatches the `secondbrain-ingester` subagent, which updates hot memory in the background. You don't manage the lifecycle — it runs itself.

---

## Bundled scheduled tasks

`init` installs the bundled scheduled tasks from `secondbrain/scheduled-tasks/MANIFEST.md` by default. You can opt out of any during setup, and customize the times:

| Task | Default time | Skill | What it does |
|---|---|---|---|
| morning-brief | 8:00am daily | `secondbrain-morning-brief` | Pre-brief subagent warmup before the main morning planning pass |
| morning-briefing | 10:30am daily | `morning-brief` | Loads context, processes overnight inbox, builds today's energy-matched plan |
| deadline-tracker | 1:00pm daily | `deadline-check` | Lightweight midday scan, auto-promotes urgent items |
| email-triage | 9:00am weekdays | `email-triage` | Reads every unread email, extracts action items (requires Gmail MCP) |
| end-of-day-capture | 7:30pm daily | `end-of-day` | Reviews day, prompts for brain dump, flushes state |
| weekly-review | 8:00pm Sundays | `weekly-review` | Full weekly audit, builds next week's plan |
| dream-protocol | 2:00am daily | `dream-protocol` | Vault maintenance, deadline promotion, manifest rebuild, link repair, git commit, hot-memory regeneration |

Scheduled tasks only run when Claude Desktop / Code is open and your computer is awake. They don't run on a remote server.

---

## Scripts and programmatic enforcement

v3 introduces a **script-first architecture**: deterministic tasks are handled by Python scripts, not agent prompts. This makes operations faster, more reliable, and enforceable via hooks.

| Script | Purpose |
|--------|---------|
| `scripts/verify_vault.py` | 10 integrity checkers (broken links, missing entities, duplicate headings, stale inbox, metadata, manifest drift, orphans, conflicts, structure, unconverted references). Supports `--modified-only`, `--fix`, `--json`. |
| `scripts/create_entity_stubs.py` | Creates entity stub files from CLI args or verify output |
| `scripts/archive_inbox.py` | Moves processed inbox items to `archive/inbox/YYYY-MM/`. `--dry-run` supported. |
| `scripts/rebuild_manifest.py` | Regenerates `_MANIFEST.md` from actual vault state (atomic write) |
| `scripts/vault_guide.py` | Dynamic vault summary — file counts, top entities, active tasks, deadlines, inbox status |
| `scripts/init_obsidian.py` | Automated setup — installs Obsidian, plugins, configures MCP, scaffolds vault |
| `scripts/setup_steps.py` | Shared setup primitives used by both init and doctor (env vars, scaffolding, vault config, marker UUID) |
| `scripts/connect_mcp_client.py` | HTTP wrapper around the Connect MCP API — lets scripts talk to Obsidian without going through Claude's tool layer |
| `scripts/vault_git.py` | Git operations on the vault — init, commit, push, reset. CLI subcommands for hooks and skills. |
| `scripts/doctor_checks.py` | Check engine for `/secondbrain:doctor` — diagnose-then-treat with dependency ordering |
| `scripts/doctor_report.py` | Merges raw subprocess doctor JSON with stronger session-layer results and renders the final report |
| `scripts/runtime_resolver.py` | Shared vaults.json, Claude Desktop config, and Obsidian MCP runtime resolution |
| `scripts/entity_resolver.py` | Shared canonical entity matching for verification, retarget suggestions, and search expansion |
| `scripts/hot_memory_schema.py` | Schema definition and validator for `brain/hot-memory.md` (the pre-computed session context) |
| `scripts/update_hot_memory.py` | The ONLY writer of `brain/hot-memory.md` — regenerate from vault state or apply incremental updates |
| `scripts/emit_hot_memory.py` | Reads hot-memory and emits the SessionStart `systemMessage` JSON (called by the hook) |
| `scripts/extract_new_turns.py` | Reads the conversation transcript + cursor, outputs a context envelope for the ingester |
| `scripts/advance_cursor.py` | Atomically advances the per-session ingest cursor after successful processing |

**Hook enforcement:** A `PreToolUse` hook (`hooks/enforce-mcp-only.sh`) blocks `Edit`/`Write`/`NotebookEdit` on vault paths and restricts `Bash` writes to a sanctioned-script allowlist. A `PostToolUse` hook (`hooks/validate-after-write.sh`) runs `verify_vault.py` after every vault write (MCP or sanctioned-script). If integrity checks fail, the agent is blocked until it fixes the issues.

**Testing:** The `tests/` suite covers the script and hook contracts end-to-end with pytest and zero external dependencies. All core scripts are exercised on macOS, Windows, and Linux.

---

## Architecture (brief)

The system has three layers (full details in [ARCHITECTURE.md](ARCHITECTURE.md)):

1. **Schema** (plugin-injected routing rules + `me/profile.md` + `glossary.md`) — static configuration and the agent's personality. The plugin ships its own routing rules and injects them at every session start via the `SessionStart` hook; `me/profile.md` in your vault holds your bio, rhythms, and preferences (user-edited, seeded by `/secondbrain:init`).
2. **Wiki** (`brain/`, `entities/`, `log.md`, `_MANIFEST.md`) — the agent-maintained memory layer
3. **Raw Sources** (`inbox/`, optional `sources/`) — unprocessed input the agent ingests

The agent never modifies the Schema layer (you do). The agent maintains the Wiki layer (you don't directly edit it — you brain-dump into the inbox and the agent routes everything). Two navigation files at the vault root: `_MANIFEST.md` (index + content catalog) and `log.md` (Karpathy-style append-only chronological audit trail).

The architecture borrows the three-layer model from [Andrej Karpathy's gist on personal LLM wikis](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f), with credit and intentional divergences for life-management vs knowledge-management orientation. See [ARCHITECTURE.md](ARCHITECTURE.md) for the full comparison.

---

## Skills

The plugin ships 13 skills. Most run automatically — you rarely invoke them by name.

| Skill | Auto-invoked when |
|---|---|
| `init` | You run `/secondbrain:init` (one-time setup) |
| `doctor` | You run `/secondbrain:doctor` for a two-turn diagnose-then-treat health check |
| `ingest` | You paste text, say "brain dump", or the Stop hook dispatches the ingester subagent |
| `knowledge-search` | You ask about your own context (people, dates, decisions, status) |
| `whats-next` | You ask "what's next" or start a session without a task |
| `email-triage` | Scheduled, or you ask to check email |
| `morning-brief` | Scheduled at your wakeup time |
| `deadline-check` | Scheduled at 1pm |
| `end-of-day` | Scheduled at evening shutdown time |
| `weekly-review` | Scheduled Sunday 8pm |
| `dream-protocol` | Scheduled 2am |
| `vault-review` | You ask for a manual audit |
| `undo-last-turn` | You ask to undo the last turn's vault changes |

---

## Common issues

### "OBSIDIAN_MCP_PORT is not set" / MCP can't connect

The plugin's `.mcp.json` references `${OBSIDIAN_MCP_PORT}` (and `${OBSIDIAN_API_KEY}`). Both must be set in your shell environment before Claude Code/Cowork starts. These values come from the Connect MCP plugin settings in Obsidian. Run `/secondbrain:init` and it'll walk you through setting them. Or set them manually:

```bash
echo 'export OBSIDIAN_API_KEY="<your-key>"' >> ~/.zshrc
echo 'export OBSIDIAN_MCP_PORT="27124"' >> ~/.zshrc  # or your actual port
source ~/.zshrc
```

Then restart Claude Code/Cowork.

### "Cowork can't see my vault"

Cowork is sandboxed — it can only access folders listed in `preferences.localAgentModeTrustedFolders` in `~/Library/Application Support/Claude/claude_desktop_config.json`. Add your vault path there, then quit and reopen Claude Desktop.

### "Scheduled tasks aren't running in Cowork"

Cowork's scheduled tasks only run when Claude Desktop is open AND your computer is awake. If you close your laptop overnight, the 2am dream-protocol won't fire — it'll run the next time Claude Desktop is open. This is a Cowork limitation, not a plugin bug.

### "I want to install the plugin in Cowork but `/plugin install` doesn't work"

Cowork doesn't support direct GitHub install for individual users — only organization marketplaces do. Use the manual ZIP upload flow described in "Quick start — Claude Cowork" above.

### "Something is broken and I don't know what"

Run `/secondbrain:doctor` — it runs the raw subprocess checks, supplements them with session-only Cowork/Code evidence where available, and tells you exactly what's failing. If any fixable checks fail, it asks "want me to fix these?" and can auto-repair those on your confirmation.

### "I want to start over"

Delete `.secondbrain-installed` from your vault directory and run `/secondbrain:init` again. The init skill will detect this as a fresh install and walk through the full flow. Your existing vault is left untouched unless you explicitly tell init to scaffold a new one.

---

## Documentation

- **[ARCHITECTURE.md](ARCHITECTURE.md)** — three-layer model, core operations, navigation files, Karpathy comparison, full skill catalog
- **[SYNC.md](SYNC.md)** — vault sync setup for 5 different methods
- **[CONTRIBUTING.md](CONTRIBUTING.md)** — how to clone, test, and submit PRs

---

## Privacy

The plugin runs **entirely on your machine**. No data leaves your laptop except:
- LLM API calls to Anthropic (the same calls you make in any Claude Code / Cowork session)
- Whatever your Obsidian sync configuration does (Obsidian Sync, iCloud, Google Drive, Syncthing — see [SYNC.md](SYNC.md))

The plugin itself sends nothing. There's no telemetry, no analytics, no remote calls. The vault is your data, on your filesystem, encrypted at rest by your OS.

The one ambient leak vector is Anthropic's LLM API: every time the agent reads a vault file, the contents are part of the prompt sent to the model. If you're storing things you don't want Anthropic to ever see, don't put them in the vault.

---

## License

MIT. See [LICENSE](LICENSE) for full text.

---

## Credits

- [Andrej Karpathy's gist on personal LLM wikis](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) — three-layer model and index/log navigation pattern
- [Obsidian](https://obsidian.md) and the [Connect MCP plugin](https://github.com/joch/obsidian-connect-mcp) — vault storage and MCP bridge
- [Dataview](https://blacksmithgu.github.io/obsidian-dataview/) — DQL query layer
- The Claude Code and Claude Cowork teams at Anthropic for the plugin and skill systems
