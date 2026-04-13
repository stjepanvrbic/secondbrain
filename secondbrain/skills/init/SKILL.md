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
  version: "3.5.17"
---

# Core Rule

Take a non-technical user from "just installed the plugin" to "fully working second brain" in under 20 minutes. **Never assume the user knows what an env var, MCP, port, or shell config is.** Walk them through everything. Where you can configure it for them, do so (with explicit permission). Where you can't, give exact step-by-step instructions with no ambiguity.

The skill is **idempotent** — running it twice is safe. Re-runs detect what's already done and only complete the missing pieces.

The skill is **environment-aware** — it detects whether it's running in Claude Code or Claude Cowork and branches accordingly.

# Prerequisites

1. Read `_MANIFEST.md` for current vault state (may not exist on first run).
2. For vault navigation, read `@${CLAUDE_PLUGIN_ROOT}/references/vault-navigation.md`.
3. For content templates, read `@${CLAUDE_PLUGIN_ROOT}/references/templates.md`.
4. For environment-specific paths and setup, read `@${CLAUDE_PLUGIN_ROOT}/references/environments.md`.
5. For bundled scheduled tasks, read `@${CLAUDE_PLUGIN_ROOT}/scheduled-tasks/MANIFEST.md`.

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

Check for `.secondbrain-installed` marker file inside the candidate vault path. If VAULT_PATH is set, check at `${VAULT_PATH}/.secondbrain-installed`. If the user specified a vault path during this init run, check at `<that-path>/.secondbrain-installed`.
- If present: this is a re-init — print "I see you've installed me before. I'll check what's still working and only fix what's broken."
- If absent: this is a first install.

## 1c. Detect prerequisites

Check each of these and store the result:

| Check | How |
|---|---|
| Obsidian installed? | `ls /Applications/Obsidian.app` (Mac), equivalent on Linux/Windows |
| Connect MCP plugin installed? | Best-effort: look for `connect-mcp` directory under any Obsidian vault config in `~/Library/Application Support/obsidian/` (Mac) |
| `OBSIDIAN_API_KEY` env var set? | Check via env var inspection (the MCP loader will show it as substituted in the connection URL if set) |
| `OBSIDIAN_MCP_PORT` env var set? | Same — check for substitution. If not set, the plugin's `.mcp.json` will fail to resolve the URL |
| Existing vault? | Try `mcp__obsidian__vault_list` with path `/` — if it returns, a vault exists |

**Cowork only:** Check `~/Library/Application Support/Claude/claude_desktop_config.json` for `preferences.localAgentModeTrustedFolders`. Note which folders are trusted — the user's vault must live inside one of them.

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

## 2c. Connect MCP plugin missing

The init script installs the Connect MCP plugin automatically using the 
Obsidian CLI: `obsidian plugin:install id=connect-mcp enable`

If the CLI isn't available, the script downloads the plugin from GitHub 
and places it in .obsidian/plugins/connect-mcp/.

If automatic installation fails, walk the user through manual install:

```
You need the Connect MCP plugin so I can read and write your vault.

1. In Obsidian, Settings → Community plugins → Browse
2. Search for: Connect MCP
3. Install → Enable

After it's enabled, scroll back to "Community plugins" → click the gear
icon next to "Connect MCP" to open its settings.

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
The Connect MCP plugin runs on a port number. By default it's 27124,
but yours might be different.

To check: in Obsidian, Settings → Community plugins → click the gear
icon next to "Connect MCP". The port number is in the plugin's settings.

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

If `ENV == Cowork` and the user's intended vault path is NOT in `preferences.localAgentModeTrustedFolders`:

```
Cowork can only access folders you've marked as "trusted." Right now,
your vault path (~/<path>) isn't in your trusted folders list.

To fix:
1. Quit Claude Desktop completely (Cmd+Q on Mac)
2. Open this file in a text editor:
   ~/Library/Application Support/Claude/claude_desktop_config.json
3. Find the `preferences.localAgentModeTrustedFolders` array (or add it if missing)
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
  - 401/403? → "API key is wrong. Re-check the key in Obsidian's Connect MCP settings vs what's in your shell config."
  - Wrong port? → "The port doesn't match. Re-check `OBSIDIAN_MCP_PORT` against Obsidian's Connect MCP port."
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
2. Check for secondbrain markers: `brain/status.md`, `entities/`
3. If markers found: print `Found secondbrain vault at <path>.`
4. Wire the MCP connection to point to this vault
5. Skip profile seeding (Step 6) if `me/profile.md` already has real content

### 4b-health. Vault health check and migration

**MANDATORY for Scenario 2. Execute every substep below IN ORDER. Do not skip any. Do not combine steps.**

#### Substep 1: Fill missing scaffolding

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/init_obsidian.py --vault-path "<path>" --skip-install
```

Never overwrites existing content. Creates missing dirs and critical files.

#### Substep 2: Run verification — capture the output

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify_vault.py "<path>" --json
```

Save the JSON output. You will use it in Substep 5 and Substep 7.

#### Substep 3: Report results to the user (MANDATORY — do not skip)

Parse the JSON output from Substep 2. Print EXACTLY this format (substitute real counts):

```
Vault health check:
  - N broken wikilinks
  - N missing entity files
  - N duplicate headings
  - N stale inbox items
  - N orphan files
  - brain/commitments.md: <present/absent>
  - _MANIFEST.md: <present/stale/missing>
  - log.md: <present/missing>
```

#### Substep 4: Explain migration needs (only if relevant)

Check for these specific v2→v3 markers and print explanation for each that applies:

- `brain/commitments.md` exists → "commitments.md is deprecated since v3. Tasks now live in brain/status.md. I'll archive commitments.md to archive/commitments-v2.md."
- Inbox has files containing `[processed:: true]` → "v3 moves processed inbox items to archive/inbox/ instead of marking them in place. I'll move them to archive."
- Missing entity files (from verify output entity-stubs check) → "N entity files are referenced but don't exist. I'll create stubs."
- `CLAUDE.md` exists at the vault root → print the legacy-CLAUDE.md note below. Do NOT auto-delete, auto-archive, or auto-rewrite it.

**Legacy CLAUDE.md note (print when `CLAUDE.md` is present at the vault root):**

```
I see a CLAUDE.md at the vault root. Since v3.3.3 the plugin no longer
ships a CLAUDE.md template — routing rules and user bio now live in
plugin-owned files and in me/profile.md. Your existing CLAUDE.md is
orphaned but harmless; I'm leaving it alone. You can delete it by hand
whenever you want, or wait for a future migration script to archive it.
```

If none apply, skip this substep.

#### Substep 5: ASK PERMISSION — do not proceed without an answer

**MANDATORY STOP POINT. Print this exact prompt and WAIT for response:**

```
I found N errors and N warnings. I can fix most of them automatically. Want me to proceed?

  1. Yes — fix everything (recommended)
  2. Show me details first
  3. Skip fixes — I'll deal with them later

Which one? (1, 2, or 3)
```

If user picks **2**: print each error from the JSON, then re-ask (1 or 3).
If user picks **3**: skip to Step 5 (scheduled tasks) — note that vault has unresolved issues.
If user picks **1**: proceed to Substep 6.

#### Substep 6: Execute fixes in order

**Migration philosophy:** Deprecated files get moved to `inbox/` so dream-protocol re-ingests them through the current routing rules. This ensures nothing is lost AND content lands wherever it belongs in the v3 structure — not hardcoded to specific files.

Run each command and report pass/fail:

1. `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/migrate_v2_to_v3.py "<path>"` — moves any deprecated files (like brain/commitments.md) into inbox/ for re-ingestion
2. `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/archive_inbox.py "<path>"` — moves already-processed inbox items (`[processed:: true]`) to archive/inbox/
3. `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/rebuild_manifest.py "<path>"` — regenerates _MANIFEST.md
4. `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify_vault.py "<path>" --fix --json` — auto-fixes duplicate headings
5. `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify_vault.py "<path>" --json > /tmp/sb-verify.json` then `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/create_entity_stubs.py "<path>" --from-json /tmp/sb-verify.json` — creates missing entity stubs

Print summary:
```
Fixed:
  - Moved N deprecated files to inbox for re-ingestion
  - Archived N processed inbox items to archive/inbox/
  - Rebuilt _MANIFEST.md
  - Auto-fixed N duplicate headings
  - Created N entity stubs
```

**Substep 7 (dream-protocol) will ingest the files moved to inbox, routing their content to the right places.**

#### Substep 7: Run dream-protocol to consolidate

Print: "Running dream-protocol to consolidate the vault..."
Invoke `/secondbrain:dream-protocol`.

#### Substep 8: Final verification

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify_vault.py "<path>" --json
```

Compare error count before/after. Print:
```
Before: N errors, M warnings
After:  N errors, M warnings
Fixed:  N errors resolved
```

If errors remain, list them — these need human judgment.

---

This is the path for users who already have a vault on another device (synced via Obsidian Sync, iCloud, etc.) or who are installing the plugin in Claude Code after using it in Cowork. **The vault may have been built under an older plugin version, so migration and health checks are essential.**

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

## Seed brain/hot-memory.md

**This step runs for ALL scenarios (fresh, connect, import).** After the vault is scaffolded (and vault_path is known) but before anything else touches the vault, seed `brain/hot-memory.md` with the T10 INITIAL_TEMPLATE so the SessionStart hook and the ingester both have a valid file to read on their next run. The helper is idempotent: on a Scenario 2 vault that already has a valid hot-memory, it's a no-op.

```bash
python3 -c "import sys; sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts'); from setup_steps import create_hot_memory_initial; from pathlib import Path; r = create_hot_memory_initial(Path('${VAULT_PATH}')); print(r.message); sys.exit(0 if r.success else 1)"
```

Print the returned message. If the call fails, surface the error and continue — the first dream-protocol run will regenerate the file via `update_hot_memory.py --regenerate`, so this is a soft fallback rather than a hard blocker.

---

# Step 4a — Vault git tracking

**This step runs for ALL scenarios (fresh, connect, import).** After the vault is scaffolded and before scheduled tasks are installed, offer the user local git tracking for the vault. Git gives them per-turn commits they can review, and enables `/secondbrain:undo-last-turn` to roll back a single agent turn. It stays LOCAL unless the user also opts into a remote.

## 4a.1 Ask about local git

Print this prompt verbatim and wait for a yes/no answer:

```
I'd like to put your vault under version control with git. This means:
  - Every change the agent makes becomes a commit you can review
  - You can roll back any single agent turn with /secondbrain:undo-last-turn
  - This stays LOCAL to your machine — I will NOT push anywhere automatically

This works alongside iCloud / Dropbox / Obsidian Sync but they sometimes
fight each other. If you sync your vault, say "no" and I'll skip git for now.

OK to enable git? (yes/no)
```

- If the user says **no**: print `Skipping git tracking — you can enable it later with /secondbrain:doctor.` and jump to Step 5. Do not block init.
- If the user says **yes**: continue to 4a.2.

## 4a.2 Initialize git in the vault

Invoke the vault_git helper:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/vault_git.py init --vault "${VAULT_PATH}"
```

Capture stdout. If the result mentions `already a git repo`, print `Git was already set up — skipping.` and continue to 4a.3 (the user may still want to add a remote to an existing repo).

If init fails, print the error and ask the user to resolve it (most likely: `git` is not on PATH). Do not advance until the user either fixes it or chooses to skip git entirely.

After init succeeds, also write the default `.gitignore` and initial commit via the same helper — the library-level `setup_git` function handles this idempotently:

```bash
python3 -c "import sys; sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts'); from setup_steps import setup_git; from pathlib import Path; r = setup_git(Path('${VAULT_PATH}')); print(r.message); sys.exit(0 if r.success else 1)"
```

Print the message and verify success before continuing.

## 4a.3 Ask about a remote

Print this prompt verbatim:

```
Do you also want to push your vault to a remote git repository for
cross-device sync? You'll need to set this up yourself outside Obsidian
(GitHub, GitLab, your own git server). I'll only push to the URL you
give me, and only if you confirm.

Enable remote push? (yes/no)
```

- If **no**: persist `with_push=false` via 4a.5 and jump to Step 5.
- If **yes**: continue to 4a.4.

## 4a.4 Collect and validate the remote URL

Ask for the URL:

```
What's the remote URL? (e.g., git@github.com:user/my-vault.git or
https://github.com/user/my-vault.git)
```

Validate the URL shape. A valid git remote URL starts with one of:
- `git@` (SSH shorthand, e.g., `git@github.com:user/repo.git`)
- `https://` (HTTPS with optional auth)
- `ssh://` (explicit SSH)
- `file://` (local bare repo, primarily for tests)

If the URL doesn't match any of those prefixes, re-ask until it does. Do not proceed with a URL that failed validation — an unvalidated URL is the exact failure mode the Forbidden Action below is designed to prevent.

Once validated, add the remote and push:

```bash
git -C "${VAULT_PATH}" remote add origin "<url>"
git -C "${VAULT_PATH}" push -u origin HEAD
```

If `git remote add` fails because origin already exists, print the existing URL and ask the user whether to overwrite. If they say yes: `git -C "${VAULT_PATH}" remote set-url origin "<url>"`, then retry the push.

If `git push` fails (wrong permissions, remote doesn't exist yet, network down, etc.), print the error verbatim and ask how to proceed:

```
The push failed:

  <error output>

What do you want to do?
  1. Retry (I'll run the push again)
  2. Skip remote (keep local git, forget the remote URL)
  3. Abort git (remove local git entirely)

Which one? (1, 2, or 3)
```

- **1 (retry)**: re-run `git -C "${VAULT_PATH}" push -u origin HEAD` and loop back to this prompt on failure.
- **2 (skip remote)**: run `git -C "${VAULT_PATH}" remote remove origin` and jump to 4a.5 with `with_push=false`.
- **3 (abort git)**: leave the repo alone (the user can clean it up with `rm -rf ${VAULT_PATH}/.git` if they really want to undo everything), but persist `with_push=false` and jump to Step 5.

## 4a.5 Persist the with_push flag in vaults.json

Whether or not the user enabled a remote, record the final `with_push` value in `~/.config/secondbrain/vaults.json` so T9's Stop hook knows whether to push after every commit:

```bash
python3 -c "import sys; sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts'); from setup_steps import add_vault_to_config, list_configured_vaults; from pathlib import Path; entries = [v for v in list_configured_vaults() if v.path == str(Path('${VAULT_PATH}').resolve())]; [add_vault_to_config(Path(v.path), v.id, v.name, v.role, with_push=${WITH_PUSH}) for v in entries]"
```

Where `${WITH_PUSH}` is the literal Python boolean `True` or `False`. This is a simple idempotent update — add_vault_to_config takes care of the compare-and-write.

Print: `Git tracking enabled (remote: yes/no, auto-push: yes/no).`

---

# Step 5 — Scheduled task installation

**MANDATORY QUESTION — ALWAYS ASK THIS BEFORE SHOWING ANY TASK LIST:**

```
Scheduled tasks are things like morning briefings, deadline checks,
and nightly vault maintenance. They run automatically on a schedule.

How should we handle scheduled tasks for this instance?

  1. Skip — another instance (e.g., Cowork) already runs them
  2. Set up CronCreate jobs on this Claude Code instance
  3. Not sure — explain more

Which one? (1, 2, or 3)
```

**You MUST wait for the user's answer before proceeding. Do NOT skip this question. Do NOT show the task list before asking this.**

If **1 (skip)**:
- Install zero scheduled tasks
- Print: `Skipping scheduled tasks — managed elsewhere. You can still invoke any skill manually anytime (e.g., /secondbrain:morning-brief, /secondbrain:dream-protocol).`
- Proceed to Step 6

If **3 (explain)**:
- Explain: "Scheduled tasks use Claude Code's CronCreate to run skills at specific times (e.g., morning briefing at 10:30am). If you already have a Cowork session that handles these, you don't need to duplicate them here. You can always run any skill manually regardless."
- Re-ask the question (1 or 2)

If **2 (set up here)**, continue with task selection:

## 5a. Task selection

Read `@${CLAUDE_PLUGIN_ROOT}/scheduled-tasks/MANIFEST.md` for the bundled tasks.

```
I'll set up these scheduled tasks (you can opt out of any):

  ☑ morning-brief         8:00am daily        Morning warmup subagent
  ☑ morning-briefing      10:30am daily       Morning context + day plan
  ☑ deadline-tracker       1:00pm daily        Auto-promote urgent items
  ☑ email-triage           9:00am weekdays     Read inbox, extract action items (requires Gmail MCP)
  ☑ end-of-day-capture     7:30pm daily        Review day, brain dump, flush state
  ☑ weekly-review          8:00pm Sundays      Full weekly audit
  ☑ dream-protocol         2:00am daily        Vault maintenance + index rebuild

Type "all" to accept, or a task name to toggle it off:
```

For each opted-in task, branch on `ENV`:

## 5a. Claude Code branch

For each task:
1. Copy `${CLAUDE_PLUGIN_ROOT}/scheduled-tasks/<task-name>/SKILL.md` to `~/Documents/Claude/Scheduled/<task-name>/SKILL.md` (creating parent dirs if needed)
2. Call `CronCreate` with:
   - `cron`: the cron string from MANIFEST.md (e.g., `30 10 * * *`)
   - `prompt`: `Run <skill from MANIFEST.md>` (e.g., `Run secondbrain-morning-brief` or `Run /secondbrain:morning-brief`)
   - `recurring`: `true`
   - `durable`: `true` (so the task persists across restarts)
3. Verify the task registered by calling `CronList` and checking it appears
4. If creation failed, log the error and continue (don't abort the whole init)

After all tasks: print `✓ Installed N scheduled tasks via CronCreate.`

## 5b. Cowork branch

`CronCreate` is not available in Cowork. Instead:

1. Print one copy-pasteable `/schedule` command for EACH opted-in row from `scheduled-tasks/MANIFEST.md`, preserving the manifest's operation target (`Run <skill>`). For the current default manifest the commands are:

```
Cowork doesn't let plugins create scheduled tasks directly. To enable
these tasks, copy each line below and paste it into the Cowork chat
(one at a time):

  /schedule "morning brief"    daily 08:00  Run secondbrain-morning-brief
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

After running these in Cowork, the listed scheduled tasks will be active.
They run when Claude Desktop is open and your computer is awake.
```

2. **Confirm registration before proceeding.** Cowork is out-of-band — the skill has no way to query whether the `/schedule` commands were actually accepted. Block here until the user acknowledges:

```
Confirm: did you paste all of the listed commands into Cowork chat and see them accepted? [y/n]
```

If the user answers `y`, continue. If `n` (or anything else), re-print the listed commands and ask again. Do not advance to Step 6 until the user confirms `y`. If the user says they want to skip scheduled tasks entirely after seeing them, treat it as the 5a "skip" path and print the same "managed elsewhere" message.

Print: `✓ Scheduled task commands prepared and confirmed in Cowork.`

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
- Open `me/profile.md` (scaffolded from `${CLAUDE_PLUGIN_ROOT}/skills/init/templates/profile.md` during Step 4)
- Replace ALL 9 `{{...}}` placeholders in `me/profile.md`. After this step, the file must contain zero literal `{{...}}` strings. Handle each placeholder as follows:
  - `{{USER_NAME}}` — replace with the user's answer from Q1
  - `{{USER_ROLE}}` — replace with the user's answer from Q2
  - `{{USER_PREFERENCES}}` — replace with the user's answer from Q3
  - `{{USER_NEXT_ROLE}}` — replace with `_not set yet — add later when relevant_` (the agent will fill this in naturally from future conversation; don't ask now)
  - `{{USER_PARTNER}}` — replace with `_not set yet — add later when relevant_` (same — fill in later if/when the user mentions a partner)
  - `{{WAKEUP_TIME}}` — replace with `8:00am` (reasonable default)
  - `{{MORNING_WINDOW}}` — replace with `8am-12pm` (reasonable default)
  - `{{AFTERNOON_WINDOW}}` — replace with `12pm-6pm` (reasonable default)
  - `{{EVENING_WINDOW}}` — replace with `6pm-10pm` (reasonable default)
- The rhythm defaults are a starting point, not a question — replace the literal placeholder strings with the defaults above; don't leave any `{{...}}` tokens behind. If the user volunteers different rhythm values in conversation, use those instead.
- Print: `Profile seeded. It'll build up naturally from here — you can always edit me/profile.md directly.`

---

# Step 7 — Finalize: ensure vault is fully healthy

**This step is NOT optional. It runs for ALL scenarios (fresh, connect, import). When init completes, the vault MUST be in a fully healthy state with zero errors.**

## 7a. Run all fix scripts

```
Finalizing — making sure everything is healthy...
```

Run these in order:
1. `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/rebuild_manifest.py "${VAULT_PATH}"` — ensure manifest matches reality
2. `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/archive_inbox.py "${VAULT_PATH}"` — move any processed inbox items to archive
3. `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify_vault.py "${VAULT_PATH}" --fix --json` — auto-fix duplicate headings, then check everything

## 7b. Fix remaining issues from verification

Read the verify output. For any remaining errors:
- Missing entity files → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/create_entity_stubs.py "${VAULT_PATH}" --from-json <output>`
- Broken wikilinks → attempt to fix by finding the correct target, or flag for user
- Invalid metadata → fix the field values directly
- Structural issues (missing dirs/files) → create them

**Keep running verify_vault.py until it reports 0 errors.** Warnings are acceptable (orphans, suggestions), errors are not.

## 7c. Run dream-protocol

Invoke `/secondbrain:dream-protocol` to consolidate the vault:
- Rebuilds the full vault index
- Processes any remaining inbox items
- Repairs wikilinks
- Appends a log entry

## 7d. Final verification

Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify_vault.py "${VAULT_PATH}" --json` one last time.
Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/vault_guide.py "${VAULT_PATH}"` to get the vault summary.

Print the results:
```
Vault health: N errors, N warnings
Files: N total | Entities: N | Active tasks: N
Inbox: N unprocessed | Last dream-protocol: just now
```

If there are still errors, print each one with a suggested fix. These are issues that need human judgment.

---

# Step 8 — Sync method (first-time only)

**Skip this step if the user already has sync configured (check if vault is inside iCloud Drive, Google Drive, or has .stfolder for Syncthing).**

```
Do you want to sync your vault across devices?

  1. Obsidian Sync ($4/month, easiest)
  2. iCloud Drive (Mac-only, free)
  3. Google Drive (free, needs desktop app)
  4. Syncthing (free, peer-to-peer)
  5. Skip for now (single-device)

Which one? (1-5)
```

For "Skip": note in the final report that sync is not configured.

---

# Step 9 — Write install marker

Write the install marker to `${VAULT_PATH}/.secondbrain-installed` with results JSON. `init_obsidian.py` does this automatically when Step 4 runs; if you got here via a custom path, confirm the marker is present and regenerate it via `init_obsidian.py --vault-path "${VAULT_PATH}" --skip-install` if missing.

Do NOT print the "Setup complete!" summary yet — Step 10 runs doctor first and gates the final success message on doctor reporting a clean state.

---

# Step 10 — Final verification via doctor

**This step is NOT optional. Init is only "done" when doctor reports a clean vault. Without this gate, init can regress to claiming success while the vault is still unhealthy.**

## 10a. Run doctor diagnose

Invoke doctor's raw diagnose mode to confirm the install is healthy:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/doctor_cli.py --diagnose --vault "${VAULT_PATH}" --json > /tmp/sb-doctor-raw.json
```

Then gather the same session-layer supplemental checks used by `/secondbrain:doctor`:

- session MCP proof by reading `_MANIFEST.md` through Obsidian MCP
- vault identity cross-check through filesystem + MCP
- scheduled-task confirmation (`CronList` in Code, confirmed `/schedule` acceptance in Cowork)

Write those supplemental results in the same JSON shape to `/tmp/sb-doctor-session.json`, then render the merged final report:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/doctor_report.py \
  --raw-json /tmp/sb-doctor-raw.json \
  --supplemental-json /tmp/sb-doctor-session.json \
  --json > /tmp/sb-doctor-merged.json
```

Parse `/tmp/sb-doctor-merged.json`. Do NOT parse the human summary line from raw doctor output.

## 10b. Branch on diagnose result

**If merged doctor reports zero failures:** proceed to 10d and print the success summary.

**If merged doctor reports any failures:** proceed to 10c to treat them.

## 10c. Run doctor in treat mode

Invoke doctor in treatment mode to auto-fix what it can:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/doctor_cli.py --treat --vault "${VAULT_PATH}" --interactive
```

`--interactive` lets treatment functions like `setup_profile` prompt the user where needed (profile seeding asks a few questions when the template still has `{{placeholder}}` tokens).

After treatment finishes, re-run diagnose to verify:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/doctor_cli.py --diagnose --vault "${VAULT_PATH}" --json > /tmp/sb-doctor-raw.json
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/doctor_report.py \
  --raw-json /tmp/sb-doctor-raw.json \
  --supplemental-json /tmp/sb-doctor-session.json \
  --json > /tmp/sb-doctor-merged.json
```

**If the merged re-diagnose is clean:** proceed to 10d.

**If there are still failures:** list each remaining failure with the escalation instruction from doctor's output. Do NOT print "Setup complete!" — tell the user exactly what manual step is needed. Offer to re-run init, or point them at `/secondbrain:doctor` for a standalone treatment turn.

## 10d. Print the Setup Complete summary (only if doctor reports clean)

**Gate:** only print this block if doctor reports clean (zero non-skip failures). If any failure remains after 10c, fall through to 10e instead.

```
Setup complete! Your secondbrain is healthy.

Environment: Claude Code
Vault: ~/vault/ (N files, N entities, N active tasks)
Profile: seeded / already populated
Scheduled tasks: N installed / skipped (managed elsewhere)
Vault health: 0 errors (doctor clean)
Sync: configured / not configured

You're ready. Try:

  "what's next?"              → get a task
  "brain dump: ..."           → ingest into vault
  "/secondbrain:doctor"       → health check anytime
  "/secondbrain:init --verify"  → re-verify
```

## 10e. Failure path — remaining issues

If doctor still reports failures after treatment, print:

```
Setup is NOT complete. Doctor reports N remaining issue(s):

  - <check name>: <failure message>
    → Manual fix: <escalation instruction from doctor output>
  ...

Run /secondbrain:doctor after addressing each issue, or re-run
/secondbrain:init once you've fixed the blockers.
```

**Never** print "Setup complete!" when doctor reports any non-skip failure. This is the hard gate — the Phase 1 "init leaves vault in verified state" loop is only closed when doctor is clean.

---

# Verify Mode (`--verify`)

When invoked with `--verify`, skip Steps 0-8. Run only verification — no creates, no installs, no side effects.

Run these checks and print pass/fail for each:

1. Environment detection (Code vs Cowork) — informational
2. `${VAULT_PATH}/.secondbrain-installed` marker exists?
3. Obsidian process running?
4. `OBSIDIAN_API_KEY` set?
5. `OBSIDIAN_MCP_PORT` set?
6. `mcp__obsidian__vault_list` returns successfully?
7. `_MANIFEST.md` exists in the vault?
8. `log.md` exists in the vault?
9. `me/profile.md` exists with non-placeholder content?
10. Standard folders present (brain/, entities/, inbox/, me/, archive/)?
11. Scheduled tasks registered? (Code: `CronList`. Cowork: confirm the `/schedule` commands were accepted)
12. Last dream-protocol run successful? (Look at the most recent `dream-protocol` entry in `log.md`)

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
  ✓ log.md exists (latest entry: 2026-04-09 02:00 dream-protocol)
  ✓ me/profile.md exists and has user content
  ✓ All standard folders present
  ✓ Bundled scheduled tasks registered
  ✓ Last dream-protocol run: 2026-04-09 02:00 (clean)

  All checks passed (12/12).
```

If any check fails, print the failing line in red with a specific fix command. Don't take any action — just report.

# Idempotency

Every step in the full setup flow must check current state before acting:
- Step 4 never overwrites existing files
- Step 4a detects an existing git repo and skips re-init; `setup_git` handles the `.gitignore` and initial-commit steps idempotently; the with_push persistence is an add-or-update into vaults.json that collapses to a no-op if the flag is already correct
- Step 5 uses `CronList` to detect already-installed tasks and skips them
- Step 6 only re-prompts if `me/profile.md` still has placeholder content
- Steps 7-10 are inherently idempotent (verification + dream-protocol + doctor)

Re-running `/secondbrain:init` after a clean install should print: `Everything looks good. Run /secondbrain:init --verify for a detailed health check.`

# Error Handling

Every step has fallback behavior:
- If a write fails: print the error, ask if the user wants to retry or skip
- If MCP connection drops mid-setup: print where you stopped, tell the user to fix and re-run `/init` (which will pick up where it left off due to idempotency)
- If `CronCreate` fails (Code): note the failure, copy the task SKILL.md to `~/Documents/Claude/Scheduled/` anyway, and print the manual command the user can run via `/schedule` later
- If a prerequisite check is ambiguous: ask the user directly rather than guessing

# Forbidden Actions

- **Never** write to a file without permission, except files inside the vault scaffolding when the user has explicitly confirmed the vault path
- **Never** run `chmod` or modify file permissions
- **Never** modify existing `me/profile.md` content beyond filling placeholders in Step 6 (seeding a fresh template). If the file already has real content, leave it alone.
- **Never** touch a legacy plugin-generated `CLAUDE.md` in a user's vault. Since v3.3.3 the plugin no longer ships a CLAUDE.md template; existing files from v3.1.x–v3.3.2 installs are orphaned but harmless. Users can leave them, delete them, or wait for a future migration script to archive them. Do NOT delete or rewrite them on the user's behalf.
- **Never** delete anything
- **Never push to the remote without explicit user confirmation of the URL.** Step 4a is the only place the plugin learns about a vault remote; the user must supply the URL verbatim, it must pass the shape validation (`git@`, `https://`, `ssh://`, `file://`), and only then may the skill run `git push`. Never infer a remote from the environment, from git config, from recent SSH keys, or from anywhere else. Every push is a user-consented action or it doesn't happen at all.
- **Never** silently fail — every error must be reported with a fix
- **Never** assume the user has CLI knowledge (no `cd`, no `vim`, no shell tricks beyond `source ~/.zshrc`)
- **Never claim setup is complete when doctor reports any non-skip failure.** Step 10 is the hard gate — the "Setup complete!" message may only be printed after `doctor_cli.py --diagnose` reports zero failures. If treatment (10c) leaves any failure unresolved, fall through to 10e and list the remaining issues. Do not print the success summary as a "close enough" approximation.

# Implementation Notes

- The step flow (Steps 0-10, plus Step 4a) is sequential — don't parallelize. The user's mental model is "I'm being walked through setup," which requires one thing at a time.
- Wait for explicit user confirmation between steps. Don't barrel through without acknowledgment.
- When asking the user to do something in Obsidian's UI, describe the click path with as much detail as possible (corner of screen, button label, etc.) — assume they've never used Obsidian before.
- All `cron` strings are 5-field standard cron (`M H DoM Mon DoW`), as `CronCreate` expects.
- For Cowork, the vault path detection is best-effort. If you can't auto-detect the workspace, ask the user where Cowork is allowed to read from.
- Step 4a delegates git work to `secondbrain/scripts/vault_git.py` (low-level primitives) and `secondbrain/scripts/setup_steps.py::setup_git` (the idempotent composition of init + gitignore + initial commit + optional remote + optional push). Never shell out directly to `git` from the skill except where Step 4a explicitly documents it for remote add/push — otherwise you lose the `StepResult` reporting that doctor and the init report both depend on.
