# Contributing to secondbrain

Contributions welcome. The plugin is built around an opinionated philosophy (see [ARCHITECTURE.md](ARCHITECTURE.md)) — major direction changes need to be discussed first, but bug fixes, new skills, documentation improvements, and Cowork-specific compatibility patches are always appreciated.

---

## Getting set up

```bash
git clone https://github.com/stjepanvrbic/secondbrain.git
cd secondbrain
```

The plugin is Markdown skill files + Python scripts in `scripts/`. There's no build step.

### Running tests

```bash
python3 -m pytest tests/ -v
```

208 tests, zero external dependencies beyond pytest. Tests are fully self-contained — they create temporary vaults, never touch real filesystems or shell configs, and clean up automatically.

### Testing changes locally

1. Create a fresh test vault: `python3 scripts/init_obsidian.py --vault-path ~/test-vault --skip-install --dry-run`
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
4. **Run the test suite:** `python3 -m pytest tests/ -v` — all 208 tests must pass
5. If you changed scripts, also test against a real vault: `python3 scripts/verify_vault.py ~/your-vault`
6. Commit with a clear message — see "Commit messages" below
7. Push and open a PR against `main`

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
- **Don't** put hardcoded paths or personal references in any skill or template — Phase 1 of the v2.5.0 work was a big audit of these
- **Don't** add telemetry or any kind of remote calls. The plugin runs entirely on the user's machine — that's a hard line
- **Don't** disable `.gitignore` exclusions unless you have a good reason (`.mcp.json` is the one exception we made because it ships as part of the plugin's MCP config)

---

## Code style

- Markdown is the primary language. Keep skill files consistent with the existing style — frontmatter at the top, ## sections, code blocks with language tags
- Python (just `verify-vault.py`): Python 3.8+, no external dependencies
- Shell commands in skills should use POSIX-compatible syntax where possible

---

## Questions?

Open a GitHub issue with the `question` label. PRs that come with a clear explanation of "what changed and why" are merged fastest.
