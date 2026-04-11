---
name: doctor
description: >
  This skill should be used when the user asks "what's wrong with my
  secondbrain", "is everything working", "diagnose my setup", "fix my
  vault", or "secondbrain not working". Runs a 13-point diagnostic and
  reports pass/fail with specific fix commands for each issue. Read-only —
  never modifies anything.
metadata:
  version: "3.3.0"
---

# Core Rule

Run a comprehensive read-only diagnostic of the secondbrain plugin install. For each check, report pass/fail with a SPECIFIC fix command if it fails. Never modify anything — `doctor` is purely diagnostic. The primary diagnostic tool is `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify_vault.py "${VAULT_PATH}" --json`. If the user wants automatic repair, send them to `/secondbrain:init` (which IS allowed to write).

# Prerequisites

1. For vault navigation, read `@${CLAUDE_PLUGIN_ROOT}/references/vault-navigation.md`.
3. For environment-specific paths, read `@${CLAUDE_PLUGIN_ROOT}/references/environments.md`.

# Execution

Run all 13 checks in order. Don't stop on the first failure — collect all results and report at the end.

## Check 1: Plugin install location

- Verify `${CLAUDE_PLUGIN_ROOT}` is defined and resolves to a real directory
- If failing: the plugin is not properly installed. Fix: `/plugin install stjepanvrbic/secondbrain` (Code) or re-upload the plugin ZIP (Cowork)

## Check 2: Environment detection

- Probe `CronList` to detect Code vs Cowork
- Always passes (informational only) — print "Environment: Claude Code" or "Environment: Claude Cowork"

## Check 3: `OBSIDIAN_API_KEY` env var

- Check whether the env var is set and non-empty
- If failing: "OBSIDIAN_API_KEY is not set. Run /secondbrain:init to set it, or add `export OBSIDIAN_API_KEY=\"<key>\"` to your shell config (~/.zshrc on Mac with zsh, ~/.bashrc on Linux)."

## Check 4: `OBSIDIAN_MCP_PORT` env var

- Check whether the env var is set and non-empty
- If failing: "OBSIDIAN_MCP_PORT is not set. The plugin's .mcp.json depends on it. Run /secondbrain:init to set it, or add `export OBSIDIAN_MCP_PORT=\"27124\"` (or whatever port your Connect MCP plugin is using) to your shell config."

## Check 5: Obsidian process running

- Best-effort check (e.g., `pgrep -i obsidian` or check for the app via `ps`)
- If failing: "Obsidian is not running. Open /Applications/Obsidian.app and try again."

## Check 6: MCP connection (Connect MCP reachable)

- Try `mcp__obsidian__vault_list` with path `/`
- If failing: print the actual error and a specific fix:
  - Connection refused → Obsidian not running, or wrong port
  - 401/403 → API key wrong
  - Timeout → firewall or wrong port

## Check 7: Vault path reachable

- Once Check 6 passes, confirm the vault has at least one file (i.e., the connection is to a real vault, not an empty one)
- If failing: "MCP connects but the vault appears empty. Make sure the vault you opened in Obsidian has at least one Markdown file. If you just installed, run /secondbrain:init to scaffold the structure."

## Check 8: `_MANIFEST.md` exists in vault

- Try `mcp__obsidian__vault_read` on `_MANIFEST.md`
- If failing: "The vault is missing _MANIFEST.md. Run /secondbrain:dream-protocol to rebuild it, or /secondbrain:init for a full setup if this is a fresh vault."

## Check 9: `CLAUDE.md` exists in vault

- Try `mcp__obsidian__vault_read` on `CLAUDE.md`
- If failing: "The vault is missing CLAUDE.md. Run /secondbrain:init to create it from the template."

## Check 10: `log.md` exists in vault

- Try `mcp__obsidian__vault_read` on `log.md`
- If failing: "The vault is missing log.md (the append-only audit trail). Run /secondbrain:init to create it, or manually create an empty log.md at the vault root."

## Check 11: `me/profile.md` has user content (not template placeholders)

- Read `me/profile.md` and check for `{{USER_NAME}}` or similar placeholder
- If still has placeholders: "Profile has not been filled in yet. Run /secondbrain:init to walk through profile setup."

## Check 12: Standard folders present

- Check existence of: `brain/`, `entities/`, `inbox/`, `me/`, `archive/`
- For each missing: list it. Fix: "Run /secondbrain:dream-protocol or /secondbrain:init to create missing structure."

## Check 13: Scheduled tasks registered

- **Code:** Call `CronList` and check that all 6 bundled tasks (or whatever subset the user opted into) are registered
- **Cowork:** Look for `<workspace>/.scheduled-tasks/` and check the SKILL.md files exist
- If any are missing: list them. Fix: "Run /secondbrain:init to install missing scheduled tasks."

## Check 14 (bonus): Last dream-protocol run successful

- Read the last few lines of `log.md`
- Find the most recent `dream-protocol` entry
- Check whether the entry text contains "issues" or "errors" — if so, the run had problems
- If no dream-protocol entries at all: "No dream-protocol runs in log.md. Either init was never run, or dream-protocol has never fired (it normally runs at 2am nightly)."
- If most recent run had issues: "Most recent dream-protocol run reported issues: <quote>. Run /secondbrain:dream-protocol manually and check the output."

# Output Format

Print a clear table at the end with all results:

```
secondbrain doctor report:

  ✓ Plugin install location
  ✓ Environment: Claude Code
  ✓ OBSIDIAN_API_KEY set
  ✗ OBSIDIAN_MCP_PORT not set
       Fix: add 'export OBSIDIAN_MCP_PORT="27124"' to ~/.zshrc, then source it
  ✓ Obsidian process running
  ✗ MCP connection failed (port 27124 connection refused)
       Fix: make sure Obsidian is open AND the Connect MCP plugin is enabled
  - Vault reachable: SKIPPED (MCP connection must work first)
  - _MANIFEST.md exists: SKIPPED
  - CLAUDE.md exists: SKIPPED
  - log.md exists: SKIPPED
  - me/profile.md has user content: SKIPPED
  - Standard folders present: SKIPPED
  - Scheduled tasks registered: SKIPPED (cannot check without MCP)
  - Last dream-protocol run: SKIPPED

  Result: 4 passed, 2 failed, 8 skipped (downstream of failures).

  Recommended action: Fix the failing checks above (start with the
  shell config), then run /secondbrain:doctor again.
```

For passing setups:

```
secondbrain doctor report:

  ✓ Plugin install location
  ✓ Environment: Claude Code
  ✓ OBSIDIAN_API_KEY set
  ✓ OBSIDIAN_MCP_PORT set (27124)
  ✓ Obsidian process running
  ✓ MCP connection works
  ✓ Vault reachable (143 files)
  ✓ _MANIFEST.md exists (last rebuilt 2026-04-09 02:00)
  ✓ CLAUDE.md exists
  ✓ log.md exists (latest entry: 2026-04-09 02:00 dream-protocol)
  ✓ me/profile.md exists with user content
  ✓ All standard folders present (brain, entities, inbox, me, archive)
  ✓ 6 scheduled tasks registered (CronList confirms)
  ✓ Last dream-protocol run: 2026-04-09 02:00 (clean, no issues)

  Result: 14/14 checks passed. Your secondbrain is healthy.
```

# Implementation Notes

- This skill is **read-only** — never write, never delete, never run `chmod`
- If a downstream check depends on an upstream one that failed, mark it SKIPPED with a note (don't try to run it)
- Each fix command should be specific and copy-pasteable
- The user can run this skill anytime — it has zero side effects
- This skill is a great first thing to suggest when a user reports any issue with the plugin

# Forbidden Actions

- Modifying any file
- Calling any MCP tool that writes
- Running shell commands that change state
- Deleting anything
- Running `/secondbrain:init` automatically (always tell the user to run it themselves)
