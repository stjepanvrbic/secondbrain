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

**`install_git_hooks.py` is required after cloning.** It wires `core.hooksPath = .githooks`, which installs the tracked `.githooks/pre-push` hook. That hook refuses to let you push a broken plugin — it runs the full test suite, checks version consistency across marketplace metadata and skill frontmatter, verifies all hook command scripts are resolvable and executable, verifies no orphan scripts, and refuses pushes whose release state is ambiguous. The hook NEVER amends commits during push — if something is wrong, you fix it and push again.

### Running tests

```bash
python3 -m pytest tests/ -v
```

The suite has 1200+ tests and zero external dependencies beyond pytest. Tests live at the repo root under `tests/` and are fully self-contained — they create temporary vaults, never touch real filesystems or shell configs, and clean up automatically.

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
4. **Run the test suite:** `python3 -m pytest tests/ -v` — the full suite must pass
5. If you changed scripts, also test against a real vault: `python3 secondbrain/scripts/verify_vault.py ~/your-vault`
6. Commit with a clear message — see "Commit messages" below
7. Push and open a PR against `main`

---

## Releasing

**Fully automatic.** Just `git push` to `main`.

The local `pre-push` hook is validation-only: it checks version consistency, packaging, and the local Claude marketplace smoke path, but it never mutates the repo or asks for a second push.

The actual release mutation happens in GitHub Actions after the push lands on `main`:

1. `Auto Release Main` computes the next patch version from the latest semver tag.
2. It rewrites `plugin.json`, `marketplace.json`, `release.json`, and all skill frontmatter in one bot-authored release commit.
3. It creates the annotated `vX.Y.Z` tag on that release commit and pushes both back to `main`.
4. The same `Auto Release Main` run builds `secondbrain-vX.Y.Z.zip`, validates it, publishes it to GitHub Releases, then downloads the published asset and validates the real uploaded artifact again.

`push.followTags` is still configured automatically via `install_git_hooks.py`, but contributors do not need to create tags manually for normal releases. The separate `Publish Release` workflow remains available for direct/manual semver tag pushes; automatic main-branch releases do not depend on that follow-on trigger.

**Why this matters:** Cowork's server-managed marketplace relies on git tags and GitHub releases to detect plugin updates. The repository now guarantees those artifacts are created automatically from `main`, and the shipped runtime identity is tracked explicitly in `secondbrain/.claude-plugin/release.json` using the release tag plus the source commit that produced that release.

### Release verification checklist

Do not stop at "push succeeded". A Cowork-facing release is only done when:

1. `git ls-remote --tags origin 'v*'` shows the new annotated tag on `origin`
2. GitHub Actions `Publish Release` passes for that tag
3. the latest GitHub release exists and includes `secondbrain-vX.Y.Z.zip`
4. Cowork updates to that version and the extracted runtime bundle's `plugin.json`
   reports the same version

If any of those fail, Cowork can continue reporting "already up to date" even
though your local clone looks correct.

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
