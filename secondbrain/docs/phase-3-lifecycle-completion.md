# Lifecycle Redesign — Phase 1-3 Completion Notes (v3.5.0)

> Developer-facing summary of the lifecycle redesign that shipped as
> v3.5.0. NOT loaded by the agent. Audience: future maintainers reasoning
> about `setup_steps.py`, the Stop hook, the ingest subagent, or the
> hot-memory flow.

## What the redesign delivered

### Phase 1 — setup foundation & MCP wiring (T1-T4, v3.3.8 → v3.4.0 base)

Phase 1 extracted the `/secondbrain:init` skill's shell out of the
monolithic `init_obsidian.py` and into a structured step library
(`scripts/setup_steps.py`). Each step is idempotent, has a dedicated
tested entry point, and writes the result back to the shared
`~/.config/secondbrain/vaults.json` state. The Connect MCP client got a
Python HTTP wrapper (`scripts/connect_mcp_client.py`) so doctor and
dream-protocol no longer shell out for every call. The MCP enforcement
hook (`hooks/enforce-mcp-only.sh`) was hardened against the Bash channel
and wired into the runtime along with `hooks/validate-after-write.sh`
extensions.

### Phase 2 — doctor-driven init & git-tracked vaults (T5-T9, v3.4.0)

Phase 2 turned `/secondbrain:doctor` into a two-turn diagnose-then-treat
flow (`scripts/doctor_checks.py` + `scripts/doctor_cli.py`) so the
operator gets an auditable fix list before anything is mutated. The init
skill now delegates env-var writing to setup_steps and runs doctor at
the end, replacing the old "print success, hope for the best" finisher.
A new git-tracking helper (`scripts/vault_git.py`) gives the skill an
idempotent "init -> git init -> first commit -> optional push" flow with
per-vault `with_push` persistence in vaults.json. T9 landed the real
Stop hook commit-per-turn discipline and introduced the `undo-last-turn`
skill for `git reset`-style recovery, plus dream-protocol Phase 6
(session log rollup into `brain/session-log.md`).

### Phase 3 — hot memory & background ingester (T10-T14, v3.4.0 → v3.5.0)

Phase 3 replaced the old "agent reads a session-start skill on every
start" flow with a pre-computed hot memory file that is regenerated
nightly and updated incrementally after every session. The chain:

- `scripts/hot_memory_schema.py` defines the schema and validator.
- `scripts/update_hot_memory.py` is the ONLY writer (regenerate mode for
  full rebuilds, apply mode for incremental diffs from the ingester).
- `scripts/validate_hot_memory.py` is the CLI validator used by doctor
  and the dream-protocol scheduled task.
- `hooks/emit-hot-memory.sh` fires on SessionStart, resolves the active
  vault, and delegates to `scripts/emit_hot_memory.py` which reads
  `brain/hot-memory.md`, runs the schema validator, optionally appends
  an "Active Project Context" section via `scripts/vault_lookup_cwd.py`,
  and emits a `{"systemMessage": ...}` block.
- `hooks/on-stop.sh` commits per turn (T9) then dispatches the
  `secondbrain-ingester` subagent (`agents/secondbrain-ingester.md`),
  which reads new turns via `scripts/extract_new_turns.py`, advances
  the cursor atomically via `scripts/advance_cursor.py`, and feeds a
  diff into `update_hot_memory.py --apply`.
- `scheduled-tasks/morning-brief/` + `agents/secondbrain-morning-brief.md`
  produce a cached morning brief that `whats-next` reads on demand.

See `docs/session-start-architecture.md` for the session-start side of
the flow and its lifecycle diagram.

## New scripts / modules introduced

| Script | Role |
| --- | --- |
| `scripts/setup_steps.py` | Library of idempotent init steps (vault creation, env vars, MCP wiring, hot-memory seeding, git setup). |
| `scripts/connect_mcp_client.py` | Python HTTP client for Connect MCP. Used by doctor, dream-protocol, and anything else that used to shell out. |
| `scripts/doctor_checks.py` | 40+ diagnostic checks — single source of truth for doctor's output. |
| `scripts/doctor_cli.py` | Two-turn CLI glue (diagnose -> treat) on top of doctor_checks. |
| `scripts/vault_git.py` | Git tracking helper: init, first commit, optional push, per-vault `with_push` memory. |
| `scripts/hot_memory_schema.py` | Schema + validator for `brain/hot-memory.md`. |
| `scripts/update_hot_memory.py` | The ONLY writer of `brain/hot-memory.md`. Regenerate or apply-incremental. |
| `scripts/validate_hot_memory.py` | CLI wrapper around the schema validator. |
| `scripts/emit_hot_memory.py` | Reader for the SessionStart hook. Reads + validates + emits systemMessage JSON. |
| `scripts/vault_lookup_cwd.py` | Matches cwd to an `entities/*.md` file (frontmatter paths, then fuzzy basename); builds the Active Project Context block. |
| `scripts/extract_new_turns.py` | Transcript tailer for the ingester. |
| `scripts/advance_cursor.py` | Atomic cursor updater (ingester only). |
| `hooks/emit-hot-memory.sh` | SessionStart hook (replaces `session-start.sh`). |
| `hooks/on-stop.sh` | Stop hook: commit-per-turn + dispatch the ingester subagent. |
| `agents/secondbrain-ingester.md` | Subagent that reads new turns, extracts facts, and calls `update_hot_memory.py --apply`. |
| `agents/secondbrain-morning-brief.md` | Subagent that builds the cached morning brief. |
| `scheduled-tasks/morning-brief/` | 08:00 cron entry that dispatches the morning-brief subagent. |
| `skills/undo-last-turn/SKILL.md` | Operator-facing recovery for "the last commit was wrong". |

## Retired files and why

| Path | Retired in | Why |
| --- | --- | --- |
| `secondbrain/skills/session-start/SKILL.md` | T11 | Agent-driven session-start bootstrap replaced by pre-computed `brain/hot-memory.md` + the emit-hot-memory hook. Saves 2-3 wasted tool calls per session and removes the drift window when the agent forgot to invoke it. |
| `secondbrain/skills/session-end/SKILL.md` | T13 | Replaced by the Stop hook's commit-per-turn discipline plus the background ingester. The agent no longer has to remember to flush anything. |
| `secondbrain/hooks/session-start.sh` | T11 | Superseded by `hooks/emit-hot-memory.sh`, which does real work (validate + emit) instead of injecting a prompt to invoke a skill. |
| `secondbrain/references/session-start-bootstrap.md` | T11 | The verbose static bootstrap is gone; the rules now live inside `brain/hot-memory.md`, which is curated by the writers rather than re-read on every session. Historical content preserved in `docs/session-start-architecture.md`. |

Everything in `secondbrain/scripts/` that existed before Phase 1 is
still referenced by at least one active SKILL.md, hook, or test —
nothing got orphaned. `vault_guide.py` specifically is still called by
`skills/init/SKILL.md` Step 7d's final-verification output and is
guarded by `tests/test_deleted_references.py::TestVaultGuideConsumerContract`.

## Breaking changes

None. The migration is strictly additive from an operator's
perspective:

- Existing vaults continue to work — the hot-memory flow is
  forward-compatible with vaults that don't yet have a `brain/hot-memory.md`
  (doctor will call it out and the init step seeds it).
- The old session-start/session-end slash commands no longer exist, so
  any hand-typed invocation will error. This is intentional: the new
  behaviour is "don't type anything, the hook already did it".
- `hooks.json` was updated to point SessionStart at
  `emit-hot-memory.sh`. If a user has a local clone that's been modified,
  they must re-pull.
- `~/.config/secondbrain/vaults.json` grew new keys (`with_push`,
  `vault_id`, `mcp_installed`, etc.). setup_steps reads them through
  `.get()` with sane defaults so pre-Phase-1 files upgrade cleanly.

## End-to-end verification (how to smoke test the install)

1. **Run doctor on the current vault:**
   ```bash
   python3 secondbrain/scripts/doctor_cli.py --vault "$VAULT_PATH"
   ```
   Should print a clean summary (or surface the specific things the
   operator needs to fix). No green-washing — if a check fails, it
   says so and the next doctor turn applies the fix.

2. **Run init on a fresh throwaway vault:**
   ```bash
   mkdir -p /tmp/sb-smoke-vault
   CLAUDE_PLUGIN_ROOT=secondbrain python3 secondbrain/scripts/init_obsidian.py /tmp/sb-smoke-vault
   ```
   Watch for: vault scaffold written, env vars exported,
   `~/.config/secondbrain/vaults.json` has a row with a non-empty
   `vault_id`, `brain/hot-memory.md` seeded from
   `scripts/hot_memory_schema.INITIAL_TEMPLATE`, git repo initialised
   if the operator consented.

3. **Fire the SessionStart hook manually:**
   ```bash
   echo '{"cwd":"/tmp/sb-smoke-vault"}' | secondbrain/hooks/emit-hot-memory.sh
   ```
   Should emit a single line of JSON with a non-empty `systemMessage`
   and no stderr on the happy path. On error the stderr message tells
   the operator what to run next.

4. **Fire the Stop hook on a no-op transcript:**
   ```bash
   echo '{"session_id":"smoke","cwd":"/tmp/sb-smoke-vault"}' | secondbrain/hooks/on-stop.sh
   ```
   Should exit 0, commit any pending changes, and dispatch the ingester
   subagent only if there are new turns to process (cursor check).

5. **Run the pre-push hook's full check:**
   ```bash
   python3 secondbrain/scripts/bump_version.py --check
   python3 -m pytest tests/test_plugin_manifest.py -q --no-header
   python3 -m pytest tests/ -q --no-header
   ```
   All three must pass for the branch to be release-ready.

## Where to look next

- `docs/session-start-architecture.md` — component diagram + lifecycle of
  a single session.
- `scripts/hot_memory_schema.py` — the single source of truth for the
  hot memory shape. Everything else in Phase 3 reads or writes this.
- `tests/test_deleted_references.py` — regression lint for the retired
  files. Makes any resurrection loud.
- `tests/test_skill_consistency.py` — more general plugin-path lint that
  catches dangling `${CLAUDE_PLUGIN_ROOT}/...` references.
