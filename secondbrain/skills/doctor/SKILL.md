---
name: doctor
description: >
  This skill should be used when the user asks "what's wrong with my
  secondbrain", "is everything working", "diagnose my setup", "fix my
  vault", or "secondbrain not working". Doctor owns environment/bootstrap
  diagnosis and repair only. It runs a read-only diagnostic (Phase 1),
  reports results, and ONLY on the user's next-turn confirmation invokes
  the treatment phase (Phase 2) for bootstrap fixes. Phase 1 never mutates anything.
metadata:
  version: "3.5.26"
---

# Core Rule

Doctor is a **two-turn diagnose-then-treat** flow:

- **Turn 1 (Phase 1 — diagnose):** strictly read-only. Run all checks,
  print the report, end with "I can fix N of these — want me to? (yes/no)",
  and **STOP**. Do not run any tool that could modify state. Do not call
  any fix function. Do not assume consent. Wait for the user's next turn.
- **Turn 2 (Phase 2 — treat):** runs **only** if the user replies with
  explicit confirmation on the next turn (yes / sure / do it / fix it).
  If the user says no, or the reply is ambiguous, doctor must NOT treat.

The primary tool for Phase 1 is:

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/doctor_cli.py --diagnose --vault "${VAULT_PATH}"
```

The primary tool for Phase 2 is:

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/doctor_cli.py --treat --vault "${VAULT_PATH}"
```

Both modes are implemented by the tested Python module
`secondbrain/scripts/doctor_checks.py`, invoked through the thin CLI
wrapper `secondbrain/scripts/doctor_cli.py`. Do not re-implement check
logic in this skill body; always call the CLI.

Doctor does NOT define final vault health. Final vault health belongs to
`/secondbrain:dream-protocol`. Read
`@${CLAUDE_PLUGIN_ROOT}/references/healthy-vault.md` before escalating
vault-state repair. Dream Protocol repairs vault state only; it does NOT
reset Cowork bridge-session state, and reinstalling/updating the plugin
does NOT clear an already bloated Cowork dispatch bridge.

# Prerequisites

1. For vault navigation, read `@${CLAUDE_PLUGIN_ROOT}/references/vault-navigation.md`.
2. For environment-specific paths, read `@${CLAUDE_PLUGIN_ROOT}/references/environments.md`.
3. Read `@${CLAUDE_PLUGIN_ROOT}/references/healthy-vault.md`.

# Phase 1 — Diagnose (Turn 1)

FORBIDDEN: Skipping `doctor_cli.py`. You MUST run the CLI command below
BEFORE doing any manual MCP checks. The CLI runs all programmatic checks
including vault verification, legacy CLAUDE.md detection, vaults.json
validation, and plugin version mismatch detection. Manual MCP checks
(vault identity cross-check, scheduled tasks) SUPPLEMENT the CLI output —
they do NOT replace it.

On the first invocation in a session, doctor **MUST NOT make any changes**.
It **MUST** print the diagnostic report, end with the "want me to fix?"
question, and then **STOP**.

1. Run `doctor_cli.py --diagnose --vault "${VAULT_PATH}"` via the `Bash`
   tool in JSON mode and capture the raw subprocess results:

   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/doctor_cli.py --diagnose --vault "${VAULT_PATH}" --json > /tmp/sb-doctor-raw.json
   ```

   This is MANDATORY — do not skip this step.
2. **Additionally, from inside the agent session**, gather the session-layer
   checks doctor_cli.py cannot do on its own:
   - **Session MCP proof (Check 6 / runtime warnings):** read `_MANIFEST.md`
     via Obsidian MCP. If the session can read it, that is the canonical
     proof that MCP is reachable from the actual agent session. In Cowork,
     raw subprocess warnings such as "session-level validation required"
     MUST be replaced with this stronger session evidence.
   - **Vault identity cross-check (Check 6.5):** read `.secondbrain-installed`
     via the filesystem at `${VAULT_PATH}`, then read the same file via
     `mcp__obsidian__vault_read`. Compare the `vault_id` field. If they
     match, report pass. If they don't, LOUDLY report the mismatch — this
     is a config conflict, not a fix target. Tell the user to reconcile
     VAULT_PATH vs the open Obsidian vault.
   - **Scheduled tasks (Check 12):** call `CronList` (Code) or Cowork's
     scheduled-task tooling / explicit user confirmation. Verify the
     bundled tasks from `scheduled-tasks/MANIFEST.md` are registered.
3. Assemble those session-layer results into the same JSON shape as
   `doctor_cli.py --diagnose --json` and render the final merged report:

   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/doctor_report.py \
     --raw-json /tmp/sb-doctor-raw.json \
     --supplemental-json /tmp/sb-doctor-session.json
   ```

   Do NOT parse or summarize the raw CLI output first. The merged report is
   the source of truth.

4. If any failure is fixable by doctor's Phase 2, end with:

   ```
   I can fix N of these — want me to? (yes/no)
   ```

   If nothing is auto-fixable, end with a list of manual actions for each
   failure. If `vault_verification` failed or warned, explicitly say that
   `/secondbrain:dream-protocol` is the repair path for those vault issues
   and offer it as the next manual step. In both cases, **STOP**. Do NOT
   proceed to Phase 2 on the same turn.

## Checks doctor runs in Phase 1

**Auto-fixable (doctor CAN fix in Phase 2):**

| Check | Fix function |
|-------|--------------|
| `manifest` (Check 8, `_MANIFEST.md` missing) | `rebuild_manifest` |
| `log_md` (Check 9, `log.md` missing) | `create_log_md` |
| `profile` (Check 10, placeholders or missing) | `setup_profile` |
| `standard_folders` (Check 11, folders missing) | `setup_vault_scaffolding` |
| `vault_identity_cross` (marker present but missing `vault_id` field) | `write_vault_id` |
| `vaults_config` (Check 0, vaults.json missing/broken) | `add_vault_to_config` |
| `cowork_memory_hygiene` (stale compatibility `MEMORY.md`, legacy `memex` artifacts) | `repair_cowork_hygiene` |

**Escalation-only (doctor CANNOT fix — tell the user):**

| Check | Escalation |
|-------|-----------|
| `plugin_root` (Check 1) | `/plugin install stjepanvrbic/secondbrain` |
| `obsidian_api_key` (Check 3, env var missing) | run `/secondbrain:init` to obtain and write a key, or set `OBSIDIAN_API_KEY` manually in your shell config |
| `obsidian_mcp_port` (Check 4, env var missing) | run `/secondbrain:init` to configure, or `export OBSIDIAN_MCP_PORT="27124"` manually |
| `obsidian_running` (Check 5) | open `/Applications/Obsidian.app` |
| `mcp_connection` (Check 6) | check the Connect MCP plugin is enabled in Obsidian |
| `vault_identity_cross` MISMATCH (Check 6.5) | reconcile `VAULT_PATH` vs the open Obsidian vault — this is a config conflict, NOT a fix target |
| `vault_identity_cross` MARKER MISSING (Check 6.5) | run `/secondbrain:init` — doctor cannot create the marker from scratch, only init can |
| `scheduled_tasks` (Check 12) | run `/secondbrain:init` to install tasks |
| `last_dream_protocol_run` (Check 13, warning) | run `/secondbrain:dream-protocol` manually |
| `core_hooks_path` (Check 15) | run `python3 secondbrain/scripts/install_git_hooks.py` from the repo |
| `hot_memory_schema` (Check 14, missing) | run `/secondbrain:dream-protocol` to regenerate `brain/hot-memory.md`, or `/secondbrain:init` if the vault has never been set up |
| `hot_memory_schema` (Check 14, invalid) | run `/secondbrain:dream-protocol` to rebuild `brain/hot-memory.md` from scratch |
| `ingest_log_recent_failures` (Check 16, warning) | investigate the specific failures listed |
| `vault_verification` (Check 18) | run `/secondbrain:dream-protocol` to fix vault errors (wikilinks, inbox archiving, orphans) |
| `legacy_claude_md` (Check 19, warning) | delete or archive `CLAUDE.md` at vault root — deprecated since v3.3.3, may pollute agent context |
| `plugin_version_mismatch` (Check 20, warning) | in Cowork, remove and reinstall the plugin from the marketplace |
| `cowork_runtime_plugin` (Cowork warning) | the latest Cowork startup did not load `secondbrain`; repair local state if needed, then start a fresh session (`new chat`, `clear`, `compact`, or full Claude restart) |
| `cowork_session_start_stamp` (Cowork warning) | the latest SessionStart fell back or no stamp exists; after repair, start a fresh session (`new chat`, `clear`, `compact`, or full Claude restart`) |
| `cowork_dispatch_bridge` (Cowork warning) | fully quit Claude Desktop, back up `~/Library/Application Support/Claude/bridge-state.json` and `~/Library/Application Support/Claude/local-agent-mode-sessions/`, clear or rename them, relaunch Claude Desktop, then retry dispatch |

# Phase 2 — Treat (Turn 2, ONLY on confirmation)

Phase 2 runs **only** if the user's next-turn reply is explicit
confirmation: "yes", "sure", "fix it", "do it", "go ahead", or similar.
If the reply is "no", silence, or anything ambiguous, doctor must NOT
treat — report the Phase 1 results again and let the user decide.

On confirmation:

1. Run `doctor_cli.py --treat --vault "${VAULT_PATH}"` via `Bash`.
2. The CLI internally re-runs the diagnostic, invokes each fixable
   check's `fix_function` from `setup_steps`, and re-runs the diagnostic
   one more time to show the post-treatment state.
3. Read the CLI's stdout and report to the user: which fixes ran, which
   succeeded, which failed, and the new diagnostic state.
4. For any remaining failures after Phase 2, escalate to the user with
   the appropriate manual action from the escalation table above. If
   `cowork_memory_hygiene` was fixed, explicitly tell the user that the
   current session does NOT retroactively gain SessionStart context —
   they still need a fresh startup event (`new chat`, `clear`, `compact`,
   or full Claude restart`).
5. If `vault_verification` reported errors or warnings, ask the user:
   "Vault has N remaining verification issues. Want me to run Dream Protocol
   to repair the vault and drive it to a clean verification state?" On confirmation,
   invoke `/secondbrain:dream-protocol`. This is the only check that
   requires a skill invocation rather than a CLI fix — the dream-protocol
   is the final vault repair path and is responsible for reaching
   `0 errors, 0 warnings`.

## What Phase 2 does NOT do

- Phase 2 NEVER installs Obsidian or the secondbrain plugin. That's the
  init skill's job.
- Phase 2 NEVER registers scheduled tasks — scheduled-task registration
  requires the agent session to call `CronCreate` directly, and the CLI
  subprocess can't do that.
- Phase 2 NEVER writes environment variables to the user's shell config.
  `OBSIDIAN_API_KEY` and `OBSIDIAN_MCP_PORT` are init's responsibility —
  doctor cannot mint a new API key or guess a port, so those failures
  always escalate to `/secondbrain:init`.

# Forbidden Actions

**In Turn 1 (Phase 1):**

- Writing files. Period.
- Calling any MCP tool that mutates (`vault_create`, `vault_update`,
  `vault_delete`, `vault_patch`, `vault_edit`, `vault_edit_line`).
- Running shell commands that change state (`chmod`, `cp`, `mv`, `rm`,
  `git commit`, `git push`, `source`, `export`).
- Invoking `doctor_cli.py --treat` (that's the Phase 2 command).
- Assuming user consent. Phase 2 requires an EXPLICIT "yes" on the next
  turn.

**In Turn 2 (Phase 2), even on confirmation:**

- Running `/secondbrain:init` automatically (always tell the user to
  run it themselves for the escalation cases).
- Mutating files outside the vault (except shell config via
  `setup_env_vars`, which is an expected escalation path).
- Deleting anything.
- Skipping the post-treatment re-diagnostic.

# Implementation Notes

- `doctor_cli.py` encapsulates the check engine. The markdown skill body
  is deliberately thin — if you find yourself re-implementing check logic
  in natural language here, STOP and put it in `doctor_checks.py` with
  unit tests.
- The CLI's `--diagnose` mode has a filesystem-state invariant enforced
  by tests: calling it must not change the vault's state hash. This
  invariant is load-bearing. Don't break it.
- Downstream checks are automatically skipped when upstream ones fail
  (e.g. manifest/log/profile are skipped when `vault_reachable` fails).
  The skill body does not need to replicate this dependency logic.
- On a healthy vault, the report should show "Your secondbrain is healthy"
  and no "want me to fix?" prompt.

# Output Format

The merged report produces a table like this — pass it through to the user verbatim:

```
secondbrain doctor report:

  [PASS] plugin_root: CLAUDE_PLUGIN_ROOT=/Users/you/.claude/plugins/...
  [PASS] environment: environment: Claude Code
  [PASS] obsidian_api_key: Session MCP probe reached Obsidian using the active Cowork config
  [PASS] obsidian_running: Session MCP probe reached _MANIFEST.md
  [PASS] mcp_connection: Session MCP probe succeeded
  [FAIL] log_md: log.md (append-only audit trail) is missing. ...
         -> doctor can fix this (runs create_log_md)
  ...

  Result: 9 passed, 1 failed, 0 warning, 0 skipped.

  I can fix 1 of these — want me to? (yes/no)
```
