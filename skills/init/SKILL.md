---
name: init
description: >
  This skill should be used when the user installs the secondbrain plugin,
  asks to "set up the plugin", "initialize my second brain", "verify my
  setup", or "fix my second brain". Walks the user through the full
  setup flow: prerequisites verification, vault scaffolding, automatic
  scheduled task installation (Code) or guided /schedule prompts (Cowork),
  profile seeding, smoke tests. Designed to be dummy-proof — assumes
  the user is NOT a software engineer.

  Supports a `--verify` mode that runs verification only (no creates,
  no installs) for diagnosing existing installs.
metadata:
  version: "2.5.0"
---

# Core Rule

Take a non-technical user from "just installed the plugin" to "fully working second brain" in under 20 minutes. **Never assume the user knows what an env var, MCP, port, or shell config is.** Walk them through everything. Where you can configure it for them, do so (with explicit permission). Where you can't, give exact step-by-step instructions with no ambiguity.

The skill is **idempotent** — running it twice is safe. Re-runs detect what's already done and only complete the missing pieces.

The skill is **environment-aware** — it detects whether it's running in Claude Code or Claude Cowork and branches accordingly.

# Prerequisites

1. Read `_MANIFEST.md` for current vault state (may not exist on first run).
2. For vault navigation, read `@${CLAUDE_PLUGIN_ROOT}/references/vault-navigation.md`.
3. For content templates, read `@${CLAUDE_PLUGIN_ROOT}/references/templates.md`.
4. For bundled scheduled tasks, read `@${CLAUDE_PLUGIN_ROOT}/scheduled-tasks/MANIFEST.md`.

# Modes

`/secondbrain:init` — full setup flow (Steps 0-10 below)
`/secondbrain:init --verify` — verification only, no side effects (skip to "Verify Mode" section at the end)

---

# Step 0 — Greet and confirm

Print a friendly intro:

```
Welcome to secondbrain! I'm going to walk you through setup.
This takes about 5 minutes — most of it is automated.

Ready to start? (yes / no)
```

If the user says no, exit cleanly with: `OK — run /secondbrain:init when you're ready.`

If the user says yes, proceed.

## 0a. Detect scenario

Ask the user ONE question to determine the setup path:

```
Which of these describes your situation?

  1. Fresh start — I don't have any notes, create everything from scratch
  2. Connect existing vault — I already have a secondbrain vault (on this 
     device or synced from another) and I want to connect to it
  3. Import notes — I have existing notes from another app (Notion, Apple
     Notes, markdown files, etc.) that I want to bring in

Which one? (1, 2, or 3)
```

Store as `SCENARIO`. This determines the vault setup path in Step 4:

- **Scenario 1 (fresh):** Create a new vault with full scaffolding
- **Scenario 2 (connect):** User points to an existing vault path. Verify it has secondbrain structure, wire MCP, skip scaffolding. Only fill in missing pieces.
- **Scenario 3 (import):** Create a new secondbrain vault, then COPY (never move, never modify) the user's existing notes into `inbox/` for processing by ingest. Ask for the source path. The plugin NEVER touches the originals.

---

# Step 1 — Detect environment and current state

## 1a. Detect Claude Code vs Cowork

Probe for `CronList`. If the tool is callable (returns a list, even an empty one), the environment is **Claude Code**. If the tool raises "tool not available" or similar, the environment is **Claude Cowork**. Store this as `ENV` for later steps.

Print: `Detected environment: Claude Code` (or `Claude Cowork`).

## 1b. Detect first-install vs re-init

Check for `~/.secondbrain-installed` marker file.
- If present: this is a re-init — print "I see you've installed me before. I'll check what's still working and only fix what's broken."
- If absent: this is a first install.

## 1c. Detect prerequisites

Check each of these and store the result:

| Check | How |
|---|---|
| Obsidian installed? | `ls /Applications/Obsidian.app` (Mac), equivalent on Linux/Windows |
| Connect MCP (Local REST API) plugin installed? | Best-effort: look for `obsidian-local-rest-api` directory under any Obsidian vault config in `~/Library/Application Support/obsidian/` (Mac) |
| `OBSIDIAN_API_KEY` env var set? | Check via env var inspection (the MCP loader will show it as substituted in the connection URL if set) |
| `OBSIDIAN_MCP_PORT` env var set? | Same — check for substitution. If not set, the plugin's `.mcp.json` will fail to resolve the URL |
| Existing vault? | Try `mcp__obsidian__vault_list` with path `/` — if it returns, a vault exists |

**Cowork only:** Check `~/Library/Application Support/Claude/claude_desktop_config.json` for `localAgentModeTrustedFolders` array. Note which folders are trusted — the user's vault must live inside one of them.

## 1d. Print status table

Print a clear status of what's already done and what needs setup:

```
Current state:
  ✓ Obsidian installed
  ✗ Connect MCP plugin: not detected (I'll walk you through installing it)
  ✗ OBSIDIAN_API_KEY: not set (I'll help you get one)
  ✗ OBSIDIAN_MCP_PORT: not set (I'll set it after we get the key)
  ✓ Vault detected at ~/vault (will adapt, not overwrite)

Let's fix the missing pieces.
```

---

# Step 2 — Prerequisites guided install

For each missing prerequisite from Step 1c, walk the user through fixing it. After each fix, re-check before proceeding. Don't move on until everything in Step 1c is green.

## 2a. Obsidian missing

```
You'll need Obsidian (free, open-source).

1. Open https://obsidian.md/download in your browser
2. Download the version for your OS
3. Open the installer and follow the prompts
4. Open Obsidian once to make sure it launches

When you're done, type "ready" and I'll continue.
```

Wait for "ready". Then re-check.

## 2b. Dataview plugin missing

```
You need the Dataview plugin so I can query your vault.

1. In Obsidian, click the Settings gear (bottom-left corner)
2. Click "Community plugins" in the left sidebar
3. If it says "Restricted mode is on", click "Turn on community plugins"
4. Click "Browse"
5. In the search box, type: Dataview
6. Click the result by Michael Brenan
7. Click "Install"
8. After install, click "Enable"

When you're done, type "ready".
```

Wait for "ready". Then re-check.

## 2c. Connect MCP (Local REST API) plugin missing

```
You need the Local REST API plugin so I can read and write your vault.

1. In Obsidian, Settings → Community plugins → Browse
2. Search for: Local REST API
3. Click the one by Adam Coddington
4. Install → Enable

After it's enabled, scroll back to "Community plugins" → click the gear
icon next to "Local REST API" to open its settings.

You should see an "API Key" field with a long random string. Click the
copy button to copy it. We'll use it in the next step.

When you have the API key copied, type "ready".
```

Wait for "ready". Then ask:

```
Paste the API key here:
```

Store the value. Move to step 2d.

## 2d. `OBSIDIAN_API_KEY` not set

If you got the API key from step 2c, ask:

```
I have your API key. To make it available to Claude Code/Cowork, I need
to add this line to your shell config:

  export OBSIDIAN_API_KEY="<key>"

The shell config is one of these files in your home directory:
  ~/.zshrc  (most common on Mac)
  ~/.bashrc (common on Linux)
  ~/.config/fish/config.fish (Fish shell)

Should I write it for you? (yes / no — if no, I'll print the line for
you to add manually)
```

If yes:
- Detect the shell from `$SHELL` env var
- Append the `export` line to the appropriate config file
- Tell the user: `Done. Restart your terminal (or run \`source ~/.zshrc\` in any open shell), then type "ready" here.`

If no:
- Print the exact line to add and which file to add it to
- Wait for "ready"

After the user confirms, re-check that `OBSIDIAN_API_KEY` is now visible.

## 2e. `OBSIDIAN_MCP_PORT` not set

```
The Local REST API plugin runs on a port number. By default it's 27124,
but yours might be different.

To check: in Obsidian, Settings → Community plugins → click the gear
icon next to "Local REST API". The "HTTP Server Port" is the number
you want.

What's your port number? (just the number, e.g., 27124)
```

After the user provides it:

```
Got it. I'll add this line to your shell config:

  export OBSIDIAN_MCP_PORT="<number>"

Should I write it for you? (yes / no)
```

Same write-or-print flow as 2d.

## 2f. Cowork-only: trusted folder check

If `ENV == Cowork` and the user's intended vault path is NOT in `localAgentModeTrustedFolders`:

```
Cowork can only access folders you've marked as "trusted." Right now,
your vault path (~/<path>) isn't in your trusted folders list.

To fix:
1. Quit Claude Desktop completely (Cmd+Q on Mac)
2. Open this file in a text editor:
   ~/Library/Application Support/Claude/claude_desktop_config.json
3. Find the "localAgentModeTrustedFolders" array (or add it if missing)
4. Add your vault path: "/Users/you/path/to/vault"
5. Save the file
6. Reopen Claude Desktop and switch back to Cowork

When done, type "ready".
```

Wait, then re-check.

---

# Step 3 — MCP connection verification

Once all prerequisites are green, verify the connection actually works.

```
Let me try connecting to your Obsidian vault...
```

Call `mcp__obsidian__vault_list` with path `/`.

- **If success:** Print `✓ Connection works! I can see your vault.` Proceed to Step 4.
- **If failure:** Diagnose specifically:
  - Connection refused? → "Obsidian isn't running. Open Obsidian and try again."
  - 401/403? → "API key is wrong. Re-check the key in Obsidian's Local REST API settings vs what's in your shell config."
  - Wrong port? → "The port doesn't match. Re-check `OBSIDIAN_MCP_PORT` against Obsidian's Local REST API port."
  - Other? → Print the actual error and a generic troubleshooting checklist.

Loop: re-prompt the user to fix and try again. Don't proceed until the connection works.

---

# Step 4 — Vault setup (branches by SCENARIO from Step 0a)

## 4a. Scenario 1 — Fresh start

```
Where would you like your vault to live?

[Recommended: ~/secondbrain-vault]

Type a path or hit enter to accept the default:
```

Once a path is chosen, run the automated setup:
```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/init_obsidian.py --vault-path "<path>" --skip-install
```

This creates the full vault structure (brain/, entities/, me/, inbox/, archive/, scratch/) and all critical files. The script never overwrites existing files.

## 4b. Scenario 2 — Connect existing vault

```
Where is your existing secondbrain vault?

Type the path (e.g., ~/vault, ~/cowork, /Users/you/Obsidian/secondbrain):
```

After the user provides a path:
1. Verify the path exists and is a directory
2. Check for secondbrain markers: `_MANIFEST.md`, `brain/status.md`, `entities/`
3. If markers found: this is a secondbrain vault — print `Found secondbrain vault at <path>.`
4. If markers missing: this is an Obsidian vault but not a secondbrain vault. Run `scripts/init_obsidian.py --vault-path "<path>" --skip-install` to fill in missing scaffolding (the script never overwrites existing files)
5. Wire the MCP connection to point to this vault
6. Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify_vault.py "<path>"` to check health
7. Skip profile seeding (Step 6) if `me/profile.md` already has real content

This is the path for users who already have a vault on another device (synced via Obsidian Sync, iCloud, etc.) or who are installing the plugin in Claude Code after using it in Cowork.

## 4c. Scenario 3 — Import existing notes

```
Where are your existing notes? I'll COPY them into the vault's inbox
for processing. Your originals will NOT be touched.

Type the path to your notes folder:
```

After the user provides a source path:
1. Create a fresh vault first (same as Scenario 1 — ask for vault path, run init_obsidian.py)
2. COPY (never move, never modify) all `.md` files from the source path into `inbox/`
3. For non-markdown files (.docx, .pdf, .txt, etc.): copy them to `inbox/` too — the ingest skill will handle or flag them
4. Preserve the original directory structure as filename prefixes: `notes/work/meeting.md` becomes `inbox/work--meeting.md`
5. Print a summary: `Copied N files into inbox/. Your originals at <source> are untouched.`
6. After the full init completes, suggest: `Run "process inbox" to have me ingest all your imported notes.`

**CRITICAL:** The plugin NEVER modifies, moves, or deletes files outside the vault. The source folder is read-only. Only copies are made.

## Vault path storage

After vault setup, the VAULT_PATH must be available to all scripts and hooks. The init script writes env vars to the shell config. If the user specified a non-default vault path, also store it in the vault's `.secondbrain-installed` marker for future reference.

---

# Step 5 — Automatic scheduled task installation (environment-specific)

Read `@${CLAUDE_PLUGIN_ROOT}/scheduled-tasks/MANIFEST.md` for the bundled tasks.

Print the 6 tasks with their default crons + a one-line description, then ask the user to confirm. Default each to "on"; the user can hit enter to accept all or type the task name to toggle:

```
I'll set up these scheduled tasks (you can opt out of any of them):

  ☑ morning-briefing      10:30am daily       Morning context + day plan
  ☑ deadline-tracker       1:00pm daily       Auto-promote urgent items
  ☑ email-triage           9:00am weekdays    Read inbox, extract action items (requires Gmail MCP)
  ☑ end-of-day-capture     7:30pm daily       Review day, brain dump, flush state
  ☑ weekly-review          8:00pm Sundays     Full weekly audit
  ☑ dream-protocol         2:00am daily       Vault maintenance + index rebuild

Type "all" to accept all (or type a task name to toggle it off, then "all" when ready):
```

For each opted-in task, branch on `ENV`:

## 5a. Claude Code branch

For each task:
1. Copy `${CLAUDE_PLUGIN_ROOT}/scheduled-tasks/<task-name>/SKILL.md` to `~/Documents/Claude/Scheduled/<task-name>/SKILL.md` (creating parent dirs if needed)
2. Call `CronCreate` with:
   - `cron`: the cron string from MANIFEST.md (e.g., `30 10 * * *`)
   - `prompt`: `Run /secondbrain:<skill-name>` (e.g., `Run /secondbrain:morning-brief`)
   - `recurring`: `true`
   - `durable`: `true` (so the task persists across restarts)
3. Verify the task registered by calling `CronList` and checking it appears
4. If creation failed, log the error and continue (don't abort the whole init)

After all tasks: print `✓ Installed N scheduled tasks via CronCreate.`

## 5b. Cowork branch

`CronCreate` is not available in Cowork. Instead:

1. Copy each task's SKILL.md to `<workspace>/.scheduled-tasks/<task-name>/SKILL.md` so Cowork can discover them
2. Print copy-pasteable `/schedule` commands for the user to run in the Cowork chat:

```
Cowork doesn't let plugins create scheduled tasks directly. To enable
these tasks, copy each line below and paste it into the Cowork chat
(one at a time):

  /schedule "morning briefing" daily 10:30  Run /secondbrain:morning-brief
  /schedule "deadline tracker" daily 13:00  Run /secondbrain:deadline-check
  /schedule "email triage"     weekdays 09:00  Run /secondbrain:email-triage
  /schedule "end of day"       daily 19:30  Run /secondbrain:end-of-day
  /schedule "weekly review"    sundays 20:00  Run /secondbrain:weekly-review
  /schedule "dream protocol"   daily 02:00  Run /secondbrain:dream-protocol

Note: Cowork's exact /schedule syntax may differ slightly from these
examples. Check the /schedule skill in Cowork for the current format
if any of these don't work — the operation/skill mapping is what
matters, the syntax around it is just how you tell Cowork.

After running these in Cowork, all 6 scheduled tasks will be active.
They run when Claude Desktop is open and your computer is awake.
```

Print: `✓ Bundled N scheduled task templates. Run the /schedule commands above to activate them.`

---

# Step 6 — Profile seeding

**Skip this step if SCENARIO is 2 (connect) and `me/profile.md` already has real (non-placeholder) content.**

Ask 2-3 quick questions. The profile builds organically through conversation — this just avoids a totally cold first session.

```
A few quick questions so I'm not starting cold:

1. What should I call you?

[wait, store as USER_NAME]

2. What do you do? (work, studies, whatever takes most of your time)

[wait, store as USER_ROLE]

3. How do you prefer I communicate? (direct/detailed, brief/casual, etc.)

[wait, store as USER_PREFERENCES]
```

After answers:
- Replace placeholders in `CLAUDE.md` (USER_NAME, USER_ROLE, USER_PREFERENCES)
- Write answers into `me/profile.md` as a structured section
- Print: `Profile seeded. It'll build up naturally from here — you can always edit me/profile.md directly.`

---

# Step 7 — Initial dream-protocol run

```
Now I'll run the dream-protocol skill to establish a baseline. This:
- Builds the vault index (_MANIFEST.md content catalog)
- Verifies wikilinks
- Appends the first entry to log.md
- Commits the initial state to git (if you have git installed)

This usually takes 1-2 minutes...
```

Invoke `/secondbrain:dream-protocol`. Capture its output. Print a summary.

---

# Step 8 — Sync method choice

```
How would you like to sync your vault across devices? (You can change
this later — pick whatever feels easiest right now.)

  1. Obsidian Sync ($4/month, easiest, runs over Obsidian's own service)
  2. iCloud Drive (Mac-only, free, can have sync conflicts)
  3. Google Drive (free, requires Google Drive desktop app)
  4. Syncthing (free, peer-to-peer, more setup)
  5. Skip for now (single-device only — you can pick later)

Which one? (1-5)
```

For each choice, write a `SYNC.md` file in the vault root with the user's chosen method and the relevant setup steps. For methods where the plugin can help (e.g., moving the vault to iCloud Drive folder), offer to do it. For methods that require manual action, print step-by-step instructions and let the user execute them at their own pace.

For "Skip for now": just write a one-line note to `SYNC.md` saying `Single-device mode. Run /secondbrain:init again to choose a sync method later.`

---

# Step 9 — Verification smoke tests

```
Let me run a few smoke tests to make sure everything works...
```

Run each test and report pass/fail:

1. **`/secondbrain:whats-next`** — invoke the skill. Should return ONE task. Even with a fresh vault, it should produce a placeholder ("Get started: write a brain dump about something on your mind") instead of crashing.
2. **`/secondbrain:knowledge-search "who am I"`** — should return content from `me/profile.md` (just-seeded in Step 6).
3. **MCP connection** — final `mcp__obsidian__vault_list` to confirm still working.
4. **Scheduled tasks visible** — call `CronList` (Code) or note the `/schedule` commands need to be run (Cowork).
5. **`log.md` exists** with at least the initial `init` entry.
6. **Vault verification** — run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify_vault.py "${VAULT_PATH}" --json` and confirm no errors.
7. **Manifest rebuild** — run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/rebuild_manifest.py "${VAULT_PATH}"` and confirm it completes.

For each: green checkmark or red X with the specific failure.

---

# Step 10 — Setup Complete report

Print the final summary:

```
✓ Setup Complete!

Environment: Claude Code
Vault: ~/vault/
Profile: me/profile.md (USER_NAME, USER_ROLE, ...)
Scheduled tasks: 6 installed (morning-briefing 10:30am, ...)
Sync: Google Drive (or whichever was chosen)
Smoke tests: 5/5 passing

You're ready! Try one of these:

  "what's next?"           → get your first task
  "brain dump: ..."        → ingest something into the vault
  "who am I"               → test knowledge search
  "/secondbrain:init --verify"  → re-run verification anytime

A few things you may still want to do manually:
  - Expand me/profile.md as you think of more context to share
  - Install Obsidian on your phone for vault access on the go
  - (Cowork only) Run the /schedule commands I printed above

Run /secondbrain:init again anytime to re-verify or fix issues.
```

Touch `~/.secondbrain-installed` to mark the install state.

---

# Verify Mode (`--verify`)

When invoked with `--verify`, skip Steps 0-8. Run only verification — no creates, no installs, no side effects.

Run these checks and print pass/fail for each:

1. Environment detection (Code vs Cowork) — informational
2. `~/.secondbrain-installed` marker exists?
3. Obsidian process running?
4. `OBSIDIAN_API_KEY` set?
5. `OBSIDIAN_MCP_PORT` set?
6. `mcp__obsidian__vault_list` returns successfully?
7. `_MANIFEST.md` exists in the vault?
8. `CLAUDE.md` exists in the vault?
9. `log.md` exists in the vault?
10. `me/profile.md` exists with non-placeholder content?
11. Standard folders present (brain/, entities/, inbox/, me/, archive/)?
12. Scheduled tasks registered? (Code: `CronList`. Cowork: check `<workspace>/.scheduled-tasks/`)
13. Last dream-protocol run successful? (Look at the most recent `dream-protocol` entry in `log.md`)

Print a summary like:

```
secondbrain verification report:

  ✓ Environment: Claude Code
  ✓ Install marker present
  ✓ Obsidian running
  ✓ OBSIDIAN_API_KEY set
  ✓ OBSIDIAN_MCP_PORT set (27124)
  ✓ MCP connection works
  ✓ _MANIFEST.md exists (last rebuilt 2026-04-08 02:00)
  ✓ CLAUDE.md exists
  ✓ log.md exists (latest entry: 2026-04-09 02:00 dream-protocol)
  ✓ me/profile.md exists and has user content
  ✓ All standard folders present
  ✓ 6 scheduled tasks registered
  ✓ Last dream-protocol run: 2026-04-09 02:00 (clean)

  All checks passed (13/13).
```

If any check fails, print the failing line in red with a specific fix command. Don't take any action — just report.

# Idempotency

Every step in the full setup flow must check current state before acting:
- Step 4 never overwrites existing files
- Step 5 uses `CronList` to detect already-installed tasks and skips them
- Step 6 only re-prompts if `me/profile.md` still has placeholder content
- Steps 7-9 are inherently idempotent (verification + dream-protocol)

Re-running `/secondbrain:init` after a clean install should print: `Everything looks good. Run /secondbrain:init --verify for a detailed health check.`

# Error Handling

Every step has fallback behavior:
- If a write fails: print the error, ask if the user wants to retry or skip
- If MCP connection drops mid-setup: print where you stopped, tell the user to fix and re-run `/init` (which will pick up where it left off due to idempotency)
- If `CronCreate` fails (Code): note the failure, copy the task SKILL.md to `~/Documents/Claude/Scheduled/` anyway, and print the manual command the user can run via `/schedule` later
- If `git init` fails: skip without error (git is optional)
- If a prerequisite check is ambiguous: ask the user directly rather than guessing

# Forbidden Actions

- **Never** write to a file without permission, except files inside the vault scaffolding when the user has explicitly confirmed the vault path
- **Never** run `chmod` or modify file permissions
- **Never** modify existing `CLAUDE.md` content without the user's explicit confirmation (the seeding in Step 6 is the only exception, and only for new vault paths)
- **Never** delete anything
- **Never** silently fail — every error must be reported with a fix
- **Never** assume the user has CLI knowledge (no `cd`, no `vim`, no shell tricks beyond `source ~/.zshrc`)

# Implementation Notes

- The 10-step flow is sequential — don't parallelize. The user's mental model is "I'm being walked through setup," which requires one thing at a time.
- Wait for explicit user confirmation between steps. Don't barrel through without acknowledgment.
- When asking the user to do something in Obsidian's UI, describe the click path with as much detail as possible (corner of screen, button label, etc.) — assume they've never used Obsidian before.
- All `cron` strings are 5-field standard cron (`M H DoM Mon DoW`), as `CronCreate` expects.
- For Cowork, the vault path detection is best-effort. If you can't auto-detect the workspace, ask the user where Cowork is allowed to read from.
