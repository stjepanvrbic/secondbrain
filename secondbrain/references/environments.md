# Environment Reference — Claude Code vs Cowork

This plugin works in both Claude Code (CLI) and Claude Cowork (Desktop). They share the same vault and skills but differ in paths, plugin management, and scheduled task setup.

## Immutable directories (hard rule)

`inbox/` and `archive/` are **immutable**. Two PreToolUse hooks enforce this:

1. **`hooks/enforce-immutability.sh`** — blocks MCP vault writes (`vault_create`, `vault_update`, `vault_patch`, `vault_edit`, `vault_edit_line`, `vault_delete`) targeting paths inside inbox/ or archive/.

2. **`hooks/enforce-immutability-bash.sh`** — blocks Bash write operations (`mv`, `rm`, `cp`, `touch`, `sed -i`, `tee`, `> redirection`) targeting inbox/ or archive/.

**Why:** raw input and historical data should never be mutated by the agent. This prevents accidental corruption, maintains a reliable audit trail, and forces content to flow through the sanctioned ingest → archive pipeline.

**The only sanctioned ways to modify inbox/ and archive/:**
- **Adding to inbox/** — user actions outside the agent (Obsidian UI, Finder, external tools)
- **Moving files into inbox/** — `scripts/migrate_v2_to_v3.py` (direct filesystem operations, recognized by the Bash hook as a sanctioned script)
- **Moving files from inbox/ to archive/inbox/** — `scripts/archive_inbox.py` (same)

**Read operations are always allowed** — `ls`, `cat`, `grep`, `find`, `head`, `tail`, `wc`, `stat`, etc. on inbox/ and archive/ paths are not blocked.

If the agent attempts a blocked operation, the hook returns exit code 2 with an actionable error message pointing to the correct sanctioned script.

**Tests:**
- `tests/test_enforce_immutability.py` — 28 tests for MCP hook (allowed paths, blocked MCP tools, errors, malformed input)
- `tests/test_enforce_immutability_bash.py` — 44 tests for Bash hook (reads allowed, unrelated commands allowed, sanctioned scripts allowed, blocked writes via mv/rm/cp/redirection/sed/touch, edge cases)

## How to detect the environment

Probe for `CronList`:
- Returns a list (even empty) → **Claude Code**
- Tool not available error → **Claude Cowork**

## Key differences

| Feature | Claude Code | Claude Cowork |
|---------|-------------|---------------|
| Plugin install | `/plugin marketplace add` + `/plugin install` | Upload ZIP or marketplace |
| Plugin cache | `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/` | `~/Library/Application Support/Claude/local-agent-mode-sessions/<workspace>/<session>/rpm/plugin_<id>/` |
| Plugin data | `~/.claude/plugins/data/<plugin>/` | `.../<session>/local_<id>/.claude/plugins/data/<plugin>/` |
| Settings | `~/.claude/settings.json` | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Scheduled tasks | `CronCreate` API → stored in `~/Documents/Claude/Scheduled/<task>/SKILL.md` | `/schedule` command in chat (Cowork manages internally) |
| MCP config | `.mcp.json` in plugin root (auto-loaded) | `mcpServers` in `claude_desktop_config.json` (manual or auto) |
| Trusted folders | Not applicable | `preferences.localAgentModeTrustedFolders` in desktop config — vault path MUST be listed |
| Hooks | `hooks/hooks.json` in plugin root | Same file, same format |

## Scheduled tasks

### Claude Code

Tasks are created via `CronCreate` and stored as SKILL.md files:

```
~/Documents/Claude/Scheduled/
  morning-briefing/SKILL.md
  deadline-tracker/SKILL.md
  email-triage/SKILL.md
  end-of-day-capture/SKILL.md
  weekly-review/SKILL.md
  dream-protocol/SKILL.md
```

To install:
1. Copy `${CLAUDE_PLUGIN_ROOT}/scheduled-tasks/<task>/SKILL.md` to `~/Documents/Claude/Scheduled/<task>/SKILL.md`
2. Call `CronCreate` with the cron string, prompt (`Run /secondbrain:<skill>`), `recurring: true`, `durable: true`
3. Verify with `CronList`

To remove: `CronDelete` with the task ID from `CronList`.

### Claude Cowork

Tasks are registered via `/schedule` commands in the Cowork chat. Cowork manages them internally — there is no supported `.scheduled-tasks/` directory to inspect or verify against.

To install: print copy-pasteable `/schedule` commands for the user:
```
/schedule "morning briefing" daily 10:30 Run /secondbrain:morning-brief
/schedule "deadline tracker" daily 13:00 Run /secondbrain:deadline-check
/schedule "email triage" weekdays 09:00 Run /secondbrain:email-triage
/schedule "end of day" daily 19:30 Run /secondbrain:end-of-day
/schedule "weekly review" sundays 20:00 Run /secondbrain:weekly-review
/schedule "dream protocol" daily 02:00 Run /secondbrain:dream-protocol
```

The exact `/schedule` syntax may vary — check Cowork's current format if commands fail.

Tasks only run when Claude Desktop is open and the computer is awake.

### Multi-instance setup

A common pattern: Cowork runs scheduled tasks, Claude Code connects to the same vault for development.

- Only ONE instance should own scheduled tasks to avoid duplicates
- The init skill asks which instance manages scheduling
- Both instances can invoke any skill manually regardless

## MCP connection

### Claude Code

The plugin's `.mcp.json` is auto-loaded. It references `${OBSIDIAN_MCP_PORT}` and `${OBSIDIAN_API_KEY}` env vars which must be set in the shell that launched Claude Code.

### Claude Cowork

MCP servers are configured in `~/Library/Application Support/Claude/claude_desktop_config.json` under `mcpServers`:

```json
{
  "mcpServers": {
    "obsidian": {
      "command": "npx",
      "args": ["mcp-remote", "http://localhost:27124/mcp", "--header", "Authorization:${AUTH}"],
      "env": { "AUTH": "Bearer <your-api-key>" }
    }
  }
}
```

The init script configures this automatically when running in Cowork.

## Vault access (Cowork only)

Cowork is sandboxed. The vault path must be in `preferences.localAgentModeTrustedFolders` inside `claude_desktop_config.json`:

```json
{
  "preferences": {
    "localAgentModeTrustedFolders": ["/Users/you/cowork"]
  }
}
```

If the vault isn't trusted, Cowork can't read or write it. The init skill detects this and walks the user through adding it.

### Desktop config overrides for scripts and tests

The shared runtime resolver supports two environment overrides:

- `SECONDBRAIN_VAULTS_CONFIG` — override the default `~/.config/secondbrain/vaults.json`
- `SECONDBRAIN_CLAUDE_DESKTOP_CONFIG` — override the default Claude Desktop config path

Scripts use these to stay aligned across Claude Code, Cowork, and tests.

## Marketplace update mechanism

### Claude Code

Claude Code maintains a local git clone at `~/.claude/plugins/marketplaces/secondbrain/`. Updates are detected via `git fetch` + comparing `metadata.version` in marketplace.json. This works reliably because the local clone has full history.

### Cowork

Cowork's marketplace is server-managed (marketplaceId-based). The plugin is NOT cloned locally by Cowork — the server maintains a clone and syncs it to connected clients. If the server clone becomes stale (no tags pushed, or history disrupted by a force-push), Cowork reports "already up to date" even when the repo has advanced.

**Tags and GitHub releases are the reliable signals that trigger the server to refresh.** The `bump_version.py --release` pipeline and pre-push hook enforce that every push has a corresponding annotated tag. GitHub Actions creates a release when the tag arrives.

## Platform paths (macOS / Linux / Windows)

| Path | macOS | Linux | Windows |
|------|-------|-------|---------|
| Claude Code config | `~/.claude/` | `~/.claude/` | `%USERPROFILE%\.claude\` |
| Cowork desktop config | `~/Library/Application Support/Claude/` | `~/.config/Claude/` | `%APPDATA%\Claude\` |
| Scheduled tasks (Code) | `~/Documents/Claude/Scheduled/` | `~/Documents/Claude/Scheduled/` | `%USERPROFILE%\Documents\Claude\Scheduled\` |
| Obsidian config | `~/Library/Application Support/obsidian/` | `~/.config/obsidian/` | `%APPDATA%\obsidian\` |
| Obsidian CLI | `/usr/local/bin/obsidian` | `/usr/bin/obsidian` or `/snap/bin/obsidian` | In PATH after install |
