---
name: undo-last-turn
description: >
  This skill should be used when the user says "undo that", "undo the last
  turn", "revert what you just did", "rollback that change", or runs
  /secondbrain:undo-last-turn. Reverts the most recent vault git commit
  (which corresponds to the last agent turn, since the Stop hook commits
  after every turn). ALWAYS confirms with the user before doing anything
  destructive — this skill is a one-way lossy operation.
metadata:
  version: "3.5.26"
---

# Core Rule

Undo the last vault git commit and restore the vault to its previous
state. **ALWAYS confirm with the user before running.** Show the user
exactly what will be discarded. If the user says anything other than a
clear "yes", stop. This skill is a destructive rollback — treat it like
`rm -rf` with a confirmation prompt.

The skill only operates on the **vault's** git repo, never on the
secondbrain plugin's own git. It's the user-facing counterpart to the
Stop hook (which commits after every turn) and the init-time git setup
(which bootstraps the repo in the first place).

# Prerequisites

1. The active vault must be registered in `~/.config/secondbrain/vaults.json`. If no active vault is set, abort and tell the user to run `/secondbrain:init`.
2. The active vault must be a git repo. If the user opted out of git during init (or never ran init), there's nothing to undo — tell them so and stop.
3. The vault must have at least **two** commits. The first commit is the initial secondbrain scaffolding and `vault_git.py reset-last-commit` refuses to drop it (see the initial-commit guard in `@${CLAUDE_PLUGIN_ROOT}/scripts/vault_git.py`). If the repo only has one commit, there is nothing to undo.

# Execution Steps

## 1. Resolve the active vault

Read `~/.config/secondbrain/vaults.json` via inline Python:

```bash
python3 -c "
import json, os, sys
from pathlib import Path
cfg = Path(os.environ.get('SECONDBRAIN_VAULTS_CONFIG', Path.home() / '.config/secondbrain/vaults.json'))
if not cfg.exists():
    sys.stderr.write('no vaults.json — run /secondbrain:init first\n'); sys.exit(1)
data = json.loads(cfg.read_text())
active_id = data.get('active_vault_id')
if not active_id:
    sys.stderr.write('no active vault set\n'); sys.exit(1)
for v in data.get('vaults', []):
    if v.get('id') == active_id:
        print(v['path']); sys.exit(0)
sys.stderr.write('active vault not found in config\n'); sys.exit(1)
"
```

Export the result as `VAULT_PATH` for the remaining steps.

## 2. Preview what will be discarded

Run `last-commit-files` to fetch the file list from HEAD. This is read-only — safe to run before asking for confirmation:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/vault_git.py last-commit-files \
    --vault "${VAULT_PATH}"
```

Also capture the HEAD commit's SHA and message for the confirmation prompt:

```bash
git -C "${VAULT_PATH}" log -1 --format='%h %s'
```

## 3. Ask the user to confirm

Print the preview to the user verbatim and ask, exactly:

> About to revert the last vault commit.
>
> HEAD: `<short-sha> <commit-message>`
>
> Files that will be discarded:
> - `<file-1>`
> - `<file-2>`
> - ...
>
> This rewrites vault state to the previous commit and CANNOT be undone via this skill. Confirm? (yes/no)

If the user replies anything other than a clear "yes" (case-insensitive, optionally followed by punctuation), stop. Print `Cancelled. Nothing was changed.` and exit. Do not ask again.

## 4. Run the reset

On confirmed yes:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/vault_git.py reset-last-commit \
    --vault "${VAULT_PATH}" --hard
```

`reset-last-commit` is a thin wrapper around `git reset --hard HEAD~1`. It:
- Refuses if there's only one commit (the initial scaffolding) — the script enforces this, not this skill.
- Discards working-tree changes along with the commit.

## 5. Report the outcome

On success:

> Reverted the last vault commit. The vault is now at the previous state.
> Previous HEAD: `<old-sha>` `<old-message>`
> New HEAD: `<new-sha>` `<new-message>`

On failure (script exit != 0), print the stderr verbatim and tell the user what's wrong. Common cases:
- "only one commit exists" → there's nothing to undo.
- "not a git repo" → the user opted out of git during init.
- git binary missing, etc. → surface the error.

# Forbidden Actions

- **Running without explicit user confirmation.** Even if the user said "undo" and then said "actually yes" — the confirmation prompt MUST be shown and a clean "yes" MUST be read back. No shortcuts.
- **Resetting more than one commit.** This skill undoes exactly one turn. If the user wants to go further back, they should use `git` directly in their vault. Refusing to extend this skill into a multi-step rollback keeps the safety story simple.
- **Touching the secondbrain plugin repo's git.** This skill operates exclusively on the user's vault git (the path resolved in Step 1). Never `cd` into `${CLAUDE_PLUGIN_ROOT}` or run git commands there. The plugin's git is managed by the plugin developers, not the user's vault tooling.
- **Running when the active vault is not registered or not a git repo.** If Prerequisites 1 or 2 fail, abort with a clear message. Do NOT try to "fix" the situation by running init or vault_git.py init — that's not this skill's job.
- **Silencing errors.** If `reset-last-commit` fails, surface the failure. Do not re-run with different flags, do not fall back to `git reset --soft`, do not "retry" automatically.
- **Committing new state after the revert.** A revert should leave the vault in a clean state matching the prior commit — do not layer new commits on top to "fix up" what the revert lost. Let the Stop hook do its normal work on the next turn.

# Error Handling

- **vaults.json missing or unreadable**: tell the user to run `/secondbrain:init` and stop.
- **No active vault**: same as above.
- **Active vault path is not a directory**: tell the user the vault is missing and stop. Do NOT attempt to recreate it.
- **Not a git repo**: tell the user git tracking was never enabled on this vault, so there's nothing to undo. Suggest they re-run `/secondbrain:init` if they want to add git tracking going forward.
- **reset-last-commit returns non-zero**: print the script's stderr verbatim and stop.
- **Single-commit repo**: the `reset-last-commit` error "cannot reset: only one commit exists (the initial scaffolding)" is expected. Display it and explain that this is the cold-start commit which cannot be removed.

# Implementation Notes

- The skill writes nothing to the vault itself — all mutation goes through `vault_git.py reset-last-commit`, which uses `git reset --hard`.
- The confirmation prompt is rendered by the agent (Claude), not by a script. The agent must wait for user input before calling `reset-last-commit`. No timeouts, no default answers.
- Do not log to `log.md` or `ingest-log.md` — this skill is a user-triggered rollback, not a routine background operation. The git reflog captures the revert for anyone who needs to forensically recover it.
