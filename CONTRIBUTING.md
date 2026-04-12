# Contributing to secondbrain

Contributions welcome. The plugin is built around an opinionated philosophy (see [ARCHITECTURE.md](ARCHITECTURE.md)) — major direction changes need to be discussed first, but bug fixes, new skills, documentation improvements, and Cowork-specific compatibility patches are always appreciated.

> **Shipped vs. repo-root docs.** The files in the repository root (`README.md`, `ARCHITECTURE.md`, `SYNC.md`, `CONTRIBUTING.md`) are for GitHub visitors and contributors — they are **not shipped** with the plugin. Only the `secondbrain/` subdirectory is packaged and installed in user environments. The source of truth for user-facing agent behavior is the skill files under `secondbrain/skills/` and the references under `secondbrain/references/`. If you change user-facing behavior, update those first; only touch the repo-root docs if they describe something that's actually drifted.

---

## Getting set up

```bash
git clone https://github.com/stjepanvrbic/secondbrain.git
cd secondbrain
python3 secondbrain/scripts/install_git_hooks.py
```

The plugin is Markdown skill files (under `secondbrain/skills/`) plus Python scripts (under `secondbrain/scripts/`). There's no build step.

**`install_git_hooks.py` is required after cloning.** It wires `core.hooksPath = .githooks`, which installs the tracked `.githooks/pre-push` hook. That hook refuses to let you push a broken plugin — it runs the full test suite, checks version consistency across `plugin.json` / `marketplace.json` (both `metadata.version` and `plugins[].version`), verifies all hook command scripts are resolvable and executable, verifies no orphan scripts, and refuses pushes whose version is not strictly greater than the last git tag. The hook NEVER amends commits during push — if something is wrong, you fix it and push again.

### Running tests

```bash
python3 -m pytest tests/ -v
```

374 tests, zero external dependencies beyond pytest. Tests live at the repo root under `tests/` and are fully self-contained — they create temporary vaults, never touch real filesystems or shell configs, and clean up automatically.

### Testing changes locally

1. Create a fresh test vault: `python3 secondbrain/scripts/init_obsidian.py --vault-path ~/test-vault --skip-install --dry-run`
2. Point a fresh Claude Code session at your local clone (symlink into `~/.claude/plugins/cache/secondbrain/<version>/`)
3. Run `/secondbrain:init --verify` against the test vault

---

## What needs help

Looking for first issues? These are good entry points:

- **Sync method documentation:** `SYNC.md` covers 5 methods. If you use a different one (e.g., Dropbox, OneDrive, NextCloud), add a section.
- **Skill drift fixes:** if you notice a skill's documented behavior doesn't match what it actually does in practice, file an issue or send a PR
- **Error messages:** every error message in the plugin should be actionable. If you hit an opaque error, file an issue with what you saw
- **Cross-platform tests:** the plugin is developed on macOS. Linux and Windows fixes are welcome
- **Cowork-specific quirks:** Cowork's `/schedule` syntax has been changing — if the patterns in `init`'s Step 5 don't match the current Cowork UI, file an issue or PR

---

## Pull request workflow

1. Fork the repo on GitHub
2. Create a branch: `git checkout -b fix/short-description`
3. Make your change
4. **Run the test suite:** `python3 -m pytest tests/ -v` — all 374 tests must pass
5. If you changed scripts, also test against a real vault: `python3 secondbrain/scripts/verify_vault.py ~/your-vault`
6. Commit with a clear message — see "Commit messages" below
7. Push and open a PR against `main`

---

## Releasing

One command: `python3 secondbrain/scripts/bump_version.py --release`

This bumps all version files, stages, commits, and creates an annotated git tag. Then `git push` carries everything (tags are pushed automatically via `push.followTags`). GitHub Actions creates a GitHub Release when the tag arrives.

The pre-push hook blocks if you skip this — it enforces that a tag `v{version}` exists and is reachable from HEAD.

**Why this matters:** Cowork's server-managed marketplace relies on git tags and GitHub releases to detect plugin updates. Versions without tags are invisible to Cowork. This pipeline ensures every push has a corresponding tag and release.

---

## Commit messages

Use conventional commits for the type prefix:

- `feat:` new feature, new skill, new template
- `fix:` bug fix
- `docs:` documentation only
- `refactor:` code change without behavior change
- `chore:` infrastructure (gitignore, CI, etc.)

For skills, name the skill in the scope:

- `feat(init): add --verify mode`
- `fix(email-triage): handle pagination beyond 500 results`
- `docs(architecture): clarify three-layer model`

Keep the subject line under 70 chars. Use the body for "why" — what problem does this solve, what alternatives were considered, what's the failure mode if you skip it.

---

## Testing scheduled task changes

If you're modifying a scheduled task or the `init` skill's scheduled-task install logic:

1. Test against your own real vault (you'll see the new behavior on the next nightly run)
2. Or run the skill manually: `/secondbrain:dream-protocol`, etc.
3. Check `log.md` for the appended entry
4. Run `/secondbrain:doctor` to confirm nothing else broke

---

## What NOT to do

- **Don't** add features that require new mandatory dependencies. Obsidian, Dataview, and Connect MCP are already a lot. New required deps will be rejected.
- **Don't** break backward compatibility without a major version bump and a clear migration path
- **Don't** put hardcoded paths or personal references in any skill or template — the v2.5.0 work was a big audit of these and regressions get caught in `tests/test_skill_consistency.py`
- **Don't** add telemetry or any kind of remote calls. The plugin runs entirely on the user's machine — that's a hard line
- **Don't** disable `.gitignore` exclusions unless you have a good reason (`.mcp.json` is the one exception we made because it ships as part of the plugin's MCP config)

---

## Code style

- Markdown is the primary language. Keep skill files consistent with the existing style — frontmatter at the top, ## sections, code blocks with language tags
- Python (the `secondbrain/scripts/` suite): Python 3.8+, no external dependencies. Type-checked with Pyright in strict-ish mode
- Shell commands in skills should use POSIX-compatible syntax where possible

---

## Questions?

Open a GitHub issue with the `question` label. PRs that come with a clear explanation of "what changed and why" are merged fastest.
