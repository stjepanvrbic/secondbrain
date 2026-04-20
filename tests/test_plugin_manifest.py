"""Plugin structure and manifest integrity tests.

Enforce-not-document. Every failure in this file is a bug the user would hit
during install or update. Runs against the live repo state, not fixtures.

Checks:
    - plugin.json version stays in lockstep with marketplace plugin version
    - skill frontmatter versions stay in lockstep with the canonical plugin version
    - marketplace.json source path resolves to a real plugin dir
    - hooks/hooks.json schema and every command resolves to an executable file
    - Every command uses ${CLAUDE_PLUGIN_ROOT} (no hardcoded paths)
    - Every script in scripts/ is referenced by hooks, docs, or allowlisted
    - No stray .DS_Store / __pycache__ / *.pyc inside anything the installer ships
    - Legacy release-asset metadata cannot creep back into the shipped plugin
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Iterable

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent      # repo root with .claude-plugin/marketplace.json
PLUGIN_ROOT = REPO_ROOT / "secondbrain"                  # shipped plugin source tree

MARKETPLACE_JSON = REPO_ROOT / ".claude-plugin" / "marketplace.json"
PLUGIN_JSON = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
HOOKS_JSON = PLUGIN_ROOT / "hooks" / "hooks.json"
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
HOOKS_DIR = PLUGIN_ROOT / "hooks"
SKILLS_DIR = PLUGIN_ROOT / "skills"

# Scripts that aren't invoked by hooks or skills but are legitimate CLI/dev tools.
# Everything else in scripts/ MUST be referenced somewhere or it's dead code.
#
# NOTE: `all_script_references()` only walks markdown inside PLUGIN_ROOT
# (secondbrain/). Since Theme 6, README/ARCHITECTURE/SYNC/CONTRIBUTING live at
# the repo root, so any script that is *only* documented from those files must
# be listed here explicitly.
CLI_ENTRYPOINT_ALLOWLIST = {
    "bump_version.py",         # dev-only: used by pre-push hook and CI
    "install_git_hooks.py",    # dev-only: contributor setup for PLUGIN repo's pre-push hook (core.hooksPath); unrelated to vault git
    "lifecycle_ingest.py",     # hook orchestration helper used by shell wrappers
    "run_ingester_job.py",     # detached ingester runner used by lifecycle_ingest.py
    "auto_release.py",         # dev-only: main-branch version bump helper used by GitHub Actions
    "validate_distribution.py",  # dev-only: marketplace layout + local Claude install validator
    "setup_steps.py",          # library-only: imported by init + doctor (wired up in T3/T6)
    "connect_mcp_client.py",   # library-only: imported by dream/hot-memory/doctor (wired up in T10/T11/T13/T14)
    "runtime_resolver.py",     # library-only: imported by connect/doctor/hot-memory
    "entity_resolver.py",      # library-only: imported by verify_vault
    "doctor_checks.py",        # library-only: imported by doctor_cli (T5)
    "doctor_cli.py",           # CLI entry: invoked by the doctor skill via Bash (T5)
    "doctor_report.py",        # CLI/helper: merges raw doctor JSON with session-layer evidence
    "hot_memory_schema.py",    # library-only: imported by validate_hot_memory + update_hot_memory (T10)
    "validate_hot_memory.py",  # CLI entry: invoked by doctor/dream-protocol/ingester (wired up in T11/T13)
    "update_hot_memory.py",    # CLI entry: invoked by dream-protocol + ingest subagent (wired up in T11/T13)
    "extract_new_turns.py",    # CLI entry: invoked by secondbrain-ingester subagent (wired up in T13)
    "advance_cursor.py",       # CLI entry: invoked by secondbrain-ingester subagent (wired up in T13)
    "cleanup_session_activity_spam.py",  # CLI entry: invoked by doctor treatment + dream-protocol Phase 0
    "reclaim_vault_git_space.py",  # CLI entry: user-invoked utility to remove legacy vault .git
    "refresh_vault_indexes.py",  # CLI entry: invoked by SessionStart hook when hot-memory is stale
    "rotate_log.py",  # CLI entry: invoked by dream-protocol Phase 5 + SessionStart (size-gated)
}

# Dirs the installer SHIPS to users. Nothing under here may contain dev cruft.
SHIPPED_DIRS = [PLUGIN_ROOT]

# Paths inside shipped dirs that are allowed to exist despite being "dev-only".
# Since Theme 6 moved tests/ out of secondbrain/, this is currently empty —
# retained as an extension point for iter_shipped_files().
SHIP_EXCEPTIONS: set[str] = set()


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def hook_commands(hooks_json: dict) -> list[str]:
    """Every `command` string found under hooks.SessionStart/PreToolUse/etc."""
    out: list[str] = []
    hooks = hooks_json.get("hooks", {})
    for entries in hooks.values():
        for entry in entries:
            for hook in entry.get("hooks", []):
                cmd = hook.get("command")
                if cmd:
                    out.append(cmd)
    return out


def resolve_command_script(cmd: str) -> Path | None:
    """
    Given a hook command like `"${CLAUDE_PLUGIN_ROOT}/hooks/emit-hot-memory.sh"`,
    resolve it to a real path inside the repo. Returns None if no ${CLAUDE_PLUGIN_ROOT}.
    """
    m = re.search(r'\$\{?CLAUDE_PLUGIN_ROOT\}?([^"\' ]+)', cmd)
    if not m:
        return None
    rel = m.group(1).lstrip("/")
    return PLUGIN_ROOT / rel


def iter_shipped_files(root: Path) -> Iterable[Path]:
    """Walk `root`, yielding every file the installer would copy."""
    for dirpath, dirnames, filenames in os.walk(root):
        # Skip allowlisted dirs
        rel_dir = Path(dirpath).relative_to(PLUGIN_ROOT)
        if any(rel_dir.parts[:1] == (e,) for e in SHIP_EXCEPTIONS):
            continue
        # Skip vcs
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for f in filenames:
            yield Path(dirpath) / f


def all_script_references() -> set[str]:
    """
    Scan every *.md and hooks.json for references like `scripts/foo.py`.
    Returns a set of basenames (e.g. {"verify_vault.py", "auto_update.py"}).
    """
    refs: set[str] = set()
    pat = re.compile(r"scripts/([a-zA-Z0-9_]+\.py)")

    # Markdown files in the plugin (skills, references, README, CONTRIBUTING, ...)
    for md in PLUGIN_ROOT.rglob("*.md"):
        if "tests" in md.parts:
            continue
        for m in pat.finditer(md.read_text(errors="replace")):
            refs.add(m.group(1))

    # hooks.json may mention scripts directly
    if HOOKS_JSON.exists():
        for m in pat.finditer(HOOKS_JSON.read_text(errors="replace")):
            refs.add(m.group(1))

    return refs


def parse_semver(v: str) -> tuple[int, int, int]:
    parts = v.split(".")
    return int(parts[0]), int(parts[1]), int(parts[2])


def extract_skill_version(path: Path) -> str | None:
    match = re.search(r'^\s*version:\s*"(.*?)"', path.read_text(), re.MULTILINE)
    return match.group(1) if match else None


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------

class TestVersionConsistency:
    def test_plugin_json_version_matches_marketplace_plugin_version(self):
        pj = load_json(PLUGIN_JSON)
        mj = load_json(MARKETPLACE_JSON)
        plugin_version = mj["plugins"][0]["version"]
        assert "version" in pj, (
            "plugin.json is missing version. Cowork installs and runtime bundles "
            "key off the shipped plugin manifest; removing this field regresses "
            "update detection."
        )
        assert pj["version"] == plugin_version, (
            f"plugin.json version {pj['version']!r} != marketplace plugin version "
            f"{plugin_version!r}. bump_version.py must keep both in lockstep."
        )

    def test_marketplace_metadata_version_matches_plugin_version(self):
        """
        marketplace.json has a top-level metadata.version that acts as the
        marketplace catalog version. Actively-maintained marketplaces
        (superpowers-marketplace 1.0.13, dev-browser-marketplace 1.0.1, etc.)
        bump this in lockstep with plugin releases; marketplaces that leave
        it frozen don't get picked up as "changed" by Cowork's update flow.

        We had ours stuck at 1.0.0 since day one and Cowork never detected
        our releases. Enforce lockstep.
        """
        mj = load_json(MARKETPLACE_JSON)
        metadata = mj.get("metadata", {})
        assert "version" in metadata, (
            "marketplace.json is missing metadata.version — this is the "
            "catalog-level version Cowork/Claude Code uses to detect "
            "marketplace updates. Add `metadata.version` and keep it in "
            "lockstep with plugin version."
        )
        plugin_version = mj["plugins"][0]["version"]
        assert metadata["version"] == plugin_version, (
            f"metadata.version {metadata['version']!r} != plugin version "
            f"{plugin_version!r}. bump_version.py must update both. "
            f"This drift is the root cause of marketplace update detection "
            f"silently not working."
        )

    def test_version_is_valid_semver(self):
        mj = load_json(MARKETPLACE_JSON)
        plugin_version = mj["plugins"][0]["version"]
        parts = plugin_version.split(".")
        assert len(parts) == 3, f"version {plugin_version!r} is not X.Y.Z"
        for p in parts:
            assert p.isdigit(), f"version component {p!r} is not numeric"

    def test_skill_frontmatter_versions_match_plugin_version(self):
        plugin = load_json(PLUGIN_JSON)
        plugin_version = plugin["version"]
        for skill_path in sorted(SKILLS_DIR.glob("*/SKILL.md")):
            skill_version = extract_skill_version(skill_path)
            assert skill_version, f"{skill_path.relative_to(REPO_ROOT)} is missing metadata.version"
            assert skill_version == plugin_version, (
                f"{skill_path.relative_to(REPO_ROOT)} metadata.version {skill_version!r} != "
                f"plugin.json version {plugin_version!r}. bump_version.py must keep every shipped "
                f"skill version in lockstep with the marketplace version."
            )

    def test_legacy_release_manifest_is_removed(self):
        legacy_release_manifest = PLUGIN_ROOT / ".claude-plugin" / "release.json"
        assert not legacy_release_manifest.exists(), (
            "secondbrain/.claude-plugin/release.json must stay removed. GitHub marketplace is the "
            "authoritative install/update path, so legacy release-asset metadata should not ship."
        )


class TestMarketplaceSchema:
    def test_marketplace_json_parses(self):
        data = load_json(MARKETPLACE_JSON)
        assert "name" in data
        assert "owner" in data
        assert isinstance(data.get("plugins"), list) and data["plugins"]

    def test_plugin_source_resolves_to_plugin_dir(self):
        data = load_json(MARKETPLACE_JSON)
        plugin = data["plugins"][0]
        source = plugin.get("source")
        assert source, "plugin has no source field"
        # Only the relative-path form is supported here. If we switch to url/git-subdir
        # this test needs adapting, which is intentional — a silent switch is a footgun.
        assert isinstance(source, str), (
            f"source must be a relative string path, got {type(source).__name__}: {source!r}. "
            f"URL/object sources break the 'marketplace == this repo' install path."
        )
        resolved = (REPO_ROOT / source).resolve()
        assert resolved.is_dir(), f"source path does not exist: {resolved}"
        assert (resolved / ".claude-plugin" / "plugin.json").is_file(), (
            f"source path is missing .claude-plugin/plugin.json: {resolved}"
        )


class TestPluginSchema:
    def test_plugin_json_required_fields(self):
        data = load_json(PLUGIN_JSON)
        for field in ("name", "description"):
            assert field in data, f"plugin.json missing required field: {field}"

    def test_plugin_json_name_matches_marketplace_entry(self):
        pj = load_json(PLUGIN_JSON)
        mj = load_json(MARKETPLACE_JSON)
        assert pj["name"] == mj["plugins"][0]["name"], (
            "plugin.json name differs from marketplace.json plugin name"
        )


class TestHooksJsonSchema:
    def test_hooks_json_parses(self):
        data = load_json(HOOKS_JSON)
        assert "hooks" in data, (
            "hooks.json must have a top-level 'hooks' key. "
            "(We regressed on this in commit 59e96d3 — do not regress again.)"
        )
        assert isinstance(data["hooks"], dict)

    def test_every_event_has_matcher_and_hooks(self):
        data = load_json(HOOKS_JSON)
        for event, entries in data["hooks"].items():
            assert isinstance(entries, list), f"{event} must be a list"
            for entry in entries:
                assert "matcher" in entry, f"{event} entry missing matcher"
                assert isinstance(entry.get("hooks"), list), (
                    f"{event} entry missing hooks array"
                )
                for hook in entry["hooks"]:
                    assert hook.get("type") == "command", (
                        f"{event} hook must be type=command, got {hook.get('type')!r}. "
                        f"prompt-type hooks crash on SessionStart."
                    )
                    assert "command" in hook, f"{event} hook missing command"

    def test_commands_use_claude_plugin_root(self):
        data = load_json(HOOKS_JSON)
        for cmd in hook_commands(data):
            assert "CLAUDE_PLUGIN_ROOT" in cmd, (
                f"hook command has no ${{CLAUDE_PLUGIN_ROOT}} prefix: {cmd!r}. "
                f"Hardcoded paths break when the plugin is cached at a different version dir."
            )

    def test_commands_resolve_to_executable_files(self):
        data = load_json(HOOKS_JSON)
        for cmd in hook_commands(data):
            script = resolve_command_script(cmd)
            assert script is not None, f"could not parse script from command: {cmd!r}"
            assert script.is_file(), (
                f"hook command points at missing file: {script} (from {cmd!r})"
            )
            assert os.access(script, os.X_OK), (
                f"hook command is not executable: {script} "
                f"— run `chmod +x {script.relative_to(PLUGIN_ROOT)}`"
            )


class TestNoOrphanScripts:
    def test_every_script_is_referenced(self):
        scripts = {p.name for p in SCRIPTS_DIR.glob("*.py") if p.name != "__init__.py"}
        refs = all_script_references()
        orphans = scripts - refs - CLI_ENTRYPOINT_ALLOWLIST
        assert not orphans, (
            f"scripts/ has orphan files: {sorted(orphans)}. "
            f"Either reference them from a hook/skill/command, add to "
            f"CLI_ENTRYPOINT_ALLOWLIST, or delete them. Orphans are how "
            f"auto_update.py went dead without anyone noticing."
        )


class TestGitHooksInstallable:
    """Ensure the tracked .githooks/pre-push and its installer are present.

    This doesn't check whether the current clone has core.hooksPath set (a
    fresh clone won't, by design — running `install_git_hooks.py` is part of
    the contributor onboarding). It only checks that the files exist so we
    don't regress on tracking them.
    """

    def test_pre_push_hook_is_tracked_and_executable(self):
        hook = REPO_ROOT / ".githooks" / "pre-push"
        assert hook.is_file(), (
            f"missing tracked hook: {hook}. "
            f"Restore .githooks/pre-push — the repo's push-safety enforcement lives there."
        )
        assert os.access(hook, os.X_OK), (
            f".githooks/pre-push is not executable — run `chmod +x {hook}`"
        )
        # Must be under source control
        r = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files", str(hook.relative_to(REPO_ROOT))],
            capture_output=True, text=True, check=True,
        )
        assert r.stdout.strip(), (
            f"{hook.relative_to(REPO_ROOT)} is not tracked by git. "
            f"Run `git add .githooks/pre-push` and commit it."
        )

    def test_install_script_exists(self):
        installer = SCRIPTS_DIR / "install_git_hooks.py"
        assert installer.is_file(), (
            "missing scripts/install_git_hooks.py — contributors have no way to "
            "wire core.hooksPath without it."
        )


class TestVersionAutomationContract:
    def test_auto_release_helper_is_bump_only(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "auto_release",
            REPO_ROOT / "secondbrain" / "scripts" / "auto_release.py",
        )
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert hasattr(mod, "should_skip_auto_release"), (
            "auto_release.py must expose should_skip_auto_release() so GitHub Actions can "
            "avoid re-triggering on its own version bump commit"
        )
        assert hasattr(mod, "next_patch_version"), (
            "auto_release.py must expose next_patch_version() so GitHub Actions can compute "
            "the next marketplace version deterministically"
        )
        assert hasattr(mod, "is_version_bump_commit_message"), (
            "auto_release.py must expose is_version_bump_commit_message() so the workflow can "
            "identify and skip its own follow-up commit"
        )


class TestNoStrayFiles:
    """No dev cruft may be tracked by git.

    What we care about is what the installer SHIPS to users, which is exactly
    what git tracks. We check `git ls-files` rather than walking the filesystem
    so we don't trip on transient __pycache__/ that pytest itself creates.
    """

    FORBIDDEN_BASENAMES = {".DS_Store", "Thumbs.db"}
    FORBIDDEN_SUFFIXES = {".pyc", ".pyo"}
    FORBIDDEN_DIR_COMPONENTS = {"__pycache__", ".pytest_cache", ".mypy_cache"}

    def _tracked_files(self) -> list[str]:
        r = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files", "-z"],
            capture_output=True, text=True, check=True,
        )
        return [f for f in r.stdout.split("\0") if f]

    def _is_forbidden(self, path: str) -> bool:
        parts = Path(path).parts
        name = parts[-1]
        if name in self.FORBIDDEN_BASENAMES:
            return True
        if any(name.endswith(sfx) for sfx in self.FORBIDDEN_SUFFIXES):
            return True
        if any(p in self.FORBIDDEN_DIR_COMPONENTS for p in parts[:-1]):
            return True
        return False

    def test_no_forbidden_files_tracked(self):
        offenders = sorted(f for f in self._tracked_files() if self._is_forbidden(f))
        assert not offenders, (
            f"Dev cruft tracked in git (would ship to users): {offenders}. "
            f"Remove with `git rm --cached <file>` then commit."
        )
