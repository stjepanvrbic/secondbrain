"""Skill-consistency lint: cheap drift protection for plugin markdown.

The plugin is mostly markdown that Claude reads at runtime. Drift between
SKILL.md files, reference docs, and the actual plugin contents is invisible
until a user hits the bad path. This test walks the plugin-internal markdown
and enforces:

    1. Every `${CLAUDE_PLUGIN_ROOT}/...` path reference resolves to a real
       file or directory inside the plugin source tree.
    2. No file mentions a path from DEPRECATED_PATHS, except files on the
       DEPRECATION_DOC_ALLOWLIST (which legitimately document the deprecation).

Scope:
    - `secondbrain/skills/**/SKILL.md`
    - `secondbrain/scheduled-tasks/**/*.md` (every markdown file, including MANIFEST.md)
    - `secondbrain/references/**/*` (every file)

NON-scope (deliberately):
    - Vault paths like `brain/status.md`, `entities/`, `inbox/`, `archive/`
      are RUNTIME paths in the user's Obsidian vault. They are NOT files in
      the plugin source and must not be checked here.
    - Scripts and Python source outside `references/` are not walked (their
      own tests cover them).

DEPRECATED_PATHS grows as later plan themes land. As of Theme 1 it covers:

- `brain/commitments.md` — the v2 task file that was replaced by
  `brain/status.md`. Stale wikilinks used to leak through
  `CLAUDE.md.template` and `_MANIFEST.md.template`.
- `CLAUDE.md` — since Theme 1 / v3.3.3, the plugin no longer ships a
  CLAUDE.md template. Routing rules are injected by the SessionStart hook
  as a compact `systemMessage` (T11: `hooks/emit-hot-memory.sh` reads
  `brain/hot-memory.md` and emits it) and user bio moved to the runtime
  `me/profile.md` file. Any surviving reference inside plugin markdown is
  either documentation of the deprecation (allowlisted) or drift (a bug).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent / "secondbrain"   # shipped plugin source tree
SKILLS_DIR = PLUGIN_ROOT / "skills"
SCHEDULED_TASKS_DIR = PLUGIN_ROOT / "scheduled-tasks"
REFERENCES_DIR = PLUGIN_ROOT / "references"
AGENTS_DIR = PLUGIN_ROOT / "agents"


# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------

# Paths that have been retired and must NOT be referenced from plugin files.
# Keep the list narrow and stable: adding a path is a lint rule that needs
# its own mechanical cleanup first. Later themes in the plan will grow this.
#
# Entries must be specific enough that substring matching does not clobber
# legitimate migration artifacts. E.g., a bare `brain/commitments` would also
# fire on `brain/commitments-v2.md` (the archive target named in init/SKILL.md),
# which is legitimate cleanup prose — not a forward reference to fix.
DEPRECATED_PATHS: tuple[str, ...] = (
    "brain/commitments.md",
    "CLAUDE.md",
)

# Files that are ALLOWED to mention deprecated paths because they explicitly
# document the deprecation (migration scripts, "this is gone, here's why"
# reference sections, init-time cleanup flows). Paths are relative to
# PLUGIN_ROOT.
#
# If you're tempted to add an entry here, first ask whether the mention is
# actually pedagogical ("brain/commitments.md is deprecated, here's what
# replaced it") or just stale ("[[brain/commitments]] is the only task file").
# The latter is a bug — fix the file, don't allowlist it.
DEPRECATION_DOC_ALLOWLIST: frozenset[str] = frozenset({
    # Explicit deprecation sections documenting what was removed.
    "references/templates.md",
    "references/vault-navigation.md",
    # init walks the user through cleaning up v2 artifacts — it MUST name them.
    # Since Theme 1 it also prints the legacy-CLAUDE.md note for v3.1.x–v3.3.2
    # users who have an orphaned plugin-generated CLAUDE.md at the vault root.
    "skills/init/SKILL.md",
    # dream-protocol names CLAUDE.md exactly once, in a "don't touch the
    # legacy file" forbidden-action. Pedagogical, not a live reference.
    # (T11 retired the session-start SKILL.md, so it's no longer listed.)
    "skills/dream-protocol/SKILL.md",
})

# Regex pulling `${CLAUDE_PLUGIN_ROOT}/...` (with or without `@` prefix and
# with or without `{}`) out of prose and code blocks alike. The path captures
# everything after `CLAUDE_PLUGIN_ROOT` up to the first terminator we care
# about: whitespace, backtick, quote, paren/bracket, or `<` (which signals a
# doc placeholder like `<task-name>`).
PLUGIN_PATH_RE = re.compile(
    r"@?\$\{?CLAUDE_PLUGIN_ROOT\}?(/[^\s`\"'<>)\]]*)"
)


# ----------------------------------------------------------------------
# File discovery
# ----------------------------------------------------------------------

def iter_linted_files() -> Iterable[Path]:
    """Yield every file Theme 7 cares about.

    Order is stable so failures are reproducible.
    """
    seen: set[Path] = set()

    def _add(p: Path) -> None:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)

    # SKILL.md files under skills/
    if SKILLS_DIR.is_dir():
        for skill_md in sorted(SKILLS_DIR.rglob("SKILL.md")):
            _add(skill_md)

    # Every markdown file under scheduled-tasks/ — picks up each task's
    # SKILL.md AND the top-level MANIFEST.md that `/init` reads at setup.
    if SCHEDULED_TASKS_DIR.is_dir():
        for md in sorted(SCHEDULED_TASKS_DIR.rglob("*.md")):
            _add(md)

    # Every file under references/ (markdown, templates, whatever)
    if REFERENCES_DIR.is_dir():
        for f in sorted(REFERENCES_DIR.rglob("*")):
            if f.is_file():
                _add(f)

    # Every markdown file under agents/ — subagent definitions (T13+)
    # live here and reference ${CLAUDE_PLUGIN_ROOT}/scripts/... paths
    # that must resolve to real files.
    if AGENTS_DIR.is_dir():
        for md in sorted(AGENTS_DIR.rglob("*.md")):
            _add(md)

    return sorted(seen)


def rel(path: Path) -> str:
    """Plugin-relative path, POSIX-style, for human-readable error messages."""
    return path.relative_to(PLUGIN_ROOT).as_posix()


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------

class TestLintScope:
    """Sanity: make sure we're actually walking files.

    A silent empty walk would turn this whole file into a no-op — the exact
    failure mode that lets drift slip through in the first place.
    """

    def test_at_least_one_skill_md_found(self):
        skills = [p for p in iter_linted_files() if p.name == "SKILL.md"]
        assert skills, (
            "skill-consistency lint found zero SKILL.md files. "
            "Either the directory layout changed or iter_linted_files() is broken."
        )

    def test_references_directory_walked(self):
        refs = [
            p for p in iter_linted_files()
            if REFERENCES_DIR in p.parents
        ]
        assert refs, (
            f"skill-consistency lint found zero files under {rel(REFERENCES_DIR)}. "
            f"Either the directory layout changed or iter_linted_files() is broken."
        )


class TestPluginPathReferencesResolve:
    """Every `${CLAUDE_PLUGIN_ROOT}/...` reference must resolve to a real
    file or directory in the plugin source.

    This is the bug class that `@${CLAUDE_PLUGIN_ROOT}/references/foo.md`
    links silently broke the moment the referenced file was renamed.
    """

    def test_all_plugin_paths_exist(self):
        missing: list[str] = []

        for f in iter_linted_files():
            try:
                text = f.read_text(errors="replace")
            except OSError as e:
                missing.append(f"{rel(f)}: could not read ({e})")
                continue

            for m in PLUGIN_PATH_RE.finditer(text):
                raw = m.group(1)
                # Strip trailing punctuation the regex couldn't know about.
                cleaned = raw.rstrip(".,;:")
                if not cleaned or cleaned == "/":
                    # Bare `${CLAUDE_PLUGIN_ROOT}` with nothing useful after.
                    continue
                # Paths ending in `/` are directory references — keep them,
                # the directory check below handles both cases uniformly.
                rel_path = cleaned.lstrip("/")
                target = PLUGIN_ROOT / rel_path
                if not target.exists():
                    missing.append(
                        f"{rel(f)}: ${{CLAUDE_PLUGIN_ROOT}}/{rel_path} "
                        f"does not exist in plugin source"
                    )

        assert not missing, (
            "Plugin-internal path references point at files that do not exist. "
            "Either create the target, fix the reference, or — if the target "
            "is a runtime vault path — stop using ${CLAUDE_PLUGIN_ROOT} for it.\n\n"
            + "\n".join(f"  - {m}" for m in missing)
        )


class TestNoDeprecatedPaths:
    """No plugin file may reference DEPRECATED_PATHS, except files on
    DEPRECATION_DOC_ALLOWLIST.

    The v3 migration retired `brain/commitments.md` in favor of
    `brain/status.md`, but the old wikilink kept leaking through
    `CLAUDE.md.template` and `_MANIFEST.md.template`. This is that bug's
    regression test — and the pattern the rest of the plan themes will
    register new deprecated paths against.
    """

    def test_no_deprecated_path_references(self):
        offenders: list[str] = []

        for f in iter_linted_files():
            rel_path = rel(f)
            if rel_path in DEPRECATION_DOC_ALLOWLIST:
                continue
            try:
                text = f.read_text(errors="replace")
            except OSError as e:
                offenders.append(f"{rel_path}: could not read ({e})")
                continue

            for lineno, line in enumerate(text.splitlines(), start=1):
                for bad in DEPRECATED_PATHS:
                    if bad in line:
                        offenders.append(
                            f"{rel_path}:{lineno}: contains deprecated path "
                            f"{bad!r}: {line.strip()[:120]}"
                        )
                        break  # one hit per line is enough

        assert not offenders, (
            "Plugin files reference deprecated paths. Either update the "
            "reference to the current path, or — if the file legitimately "
            "documents the deprecation — add it to DEPRECATION_DOC_ALLOWLIST.\n\n"
            + "\n".join(f"  - {o}" for o in offenders)
        )

    def test_allowlist_entries_actually_exist(self):
        """Catch allowlist rot — if a file is removed or renamed, its
        allowlist entry becomes a trap that silently exempts nothing.
        """
        for entry in DEPRECATION_DOC_ALLOWLIST:
            target = PLUGIN_ROOT / entry
            assert target.is_file(), (
                f"DEPRECATION_DOC_ALLOWLIST references {entry!r}, but that "
                f"file does not exist under {PLUGIN_ROOT}. Remove stale "
                f"allowlist entries."
            )

    def test_allowlist_entries_actually_mention_deprecated_paths(self):
        """If a file is on the allowlist but no longer mentions any
        deprecated path, the entry is dead weight and should be removed so
        the lint can catch a future regression in that file.
        """
        for entry in DEPRECATION_DOC_ALLOWLIST:
            target = PLUGIN_ROOT / entry
            text = target.read_text(errors="replace")
            if not any(bad in text for bad in DEPRECATED_PATHS):
                pytest.fail(
                    f"{entry} is on DEPRECATION_DOC_ALLOWLIST but no longer "
                    f"mentions any path in DEPRECATED_PATHS. Remove the "
                    f"allowlist entry — leaving it in place would silently "
                    f"exempt a future regression."
                )


# ----------------------------------------------------------------------
# Theme 2 single-sourcing: required reference loads
# ----------------------------------------------------------------------
#
# Single-sourcing only helps if the consuming skills actually load the
# canonical file. If a Theme 2 refactor stops loading `ingestion-rules.md`
# in `ingest/SKILL.md`, the rules silently diverge again and the whole
# point of Theme 2 is defeated. These tests enforce the load contract:
# each canonical reference has a fixed set of consuming skills, and each
# consumer must load it via `@${CLAUDE_PLUGIN_ROOT}/references/<name>.md`.
#
# Adding a consumer to this map is a deliberate lint rule — only do it
# when the skill genuinely needs the shared rules. Removing a consumer is
# a deliberate decoupling — do it only when the skill no longer writes to
# the vault / queries DQL / invokes the script suite.

REQUIRED_REFERENCE_LOADS: dict[str, frozenset[str]] = {
    # Shared write rules (wikilinks, metadata field order, atomic sections,
    # entity stub creation, no-new-task-files, immediate flush). Every skill
    # that writes to the vault loads these.
    #
    # T13 retired skills/session-end (the flush discipline moved to the
    # Stop hook + background ingester); it's no longer a consumer.
    # T13 also moved ingest's actual work into the secondbrain-ingester
    # subagent definition — the ingest skill is now a thin dispatcher
    # that doesn't load ingestion-rules.md directly. The subagent does.
    "references/ingestion-rules.md": frozenset({
        "skills/dream-protocol/SKILL.md",
        "skills/email-triage/SKILL.md",
    }),
}


class TestRequiredReferenceLoads:
    """Every Theme 2 canonical reference must be loaded by every skill
    that depends on its content.
    """

    def test_all_required_loads_present(self):
        missing: list[str] = []

        for ref_path, consumers in REQUIRED_REFERENCE_LOADS.items():
            # Sanity: the target file itself exists.
            target = PLUGIN_ROOT / ref_path
            assert target.exists(), (
                f"REQUIRED_REFERENCE_LOADS lists {ref_path!r}, but that "
                f"file does not exist. Fix the map or create the file."
            )

            for consumer_rel in consumers:
                consumer = PLUGIN_ROOT / consumer_rel
                if not consumer.exists():
                    missing.append(
                        f"{consumer_rel}: consumer declared in "
                        f"REQUIRED_REFERENCE_LOADS does not exist"
                    )
                    continue
                text = consumer.read_text(errors="replace")
                # Match both `@${CLAUDE_PLUGIN_ROOT}/references/<name>.md`
                # and bare `${CLAUDE_PLUGIN_ROOT}/references/<name>.md`.
                if ref_path not in text:
                    missing.append(
                        f"{consumer_rel}: does not load "
                        f"@${{CLAUDE_PLUGIN_ROOT}}/{ref_path}"
                    )

        assert not missing, (
            "Theme 2 single-sourcing contract violated — consumer skills "
            "are not loading their canonical reference. Add the @-load "
            "directive or remove the skill from REQUIRED_REFERENCE_LOADS.\n\n"
            + "\n".join(f"  - {m}" for m in missing)
        )
