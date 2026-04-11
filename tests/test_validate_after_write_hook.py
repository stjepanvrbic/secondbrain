"""Tests for validate-after-write.sh — PostToolUse hook that runs verify_vault.py
after vault-mutating tool calls.

Strategy: use a vault that's INTENTIONALLY broken (missing required files +
broken wikilinks) so that a genuine call to verify_vault.py exits non-zero.
Then we can distinguish "hook ran verify" (exit 2) from "hook skipped verify"
(exit 0) per branch:

    MCP vault write (mcp__obsidian__vault_*)
        → always run verify_vault.py          → exit 2 on broken vault

    Bash
        - Command invokes a vault-touching sanctioned script
          (archive_inbox.py, update_hot_memory.py, ...)   → run verify   → exit 2
        - Command invokes a non-vault-touching sanctioned script
          (bump_version.py, setup_steps.py, ...)          → skip verify  → exit 0
        - Command is anything else (ls, git, pytest, ...) → skip verify  → exit 0
"""

from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path
from typing import Iterator

import pytest

HOOK = Path(__file__).resolve().parent.parent / "secondbrain" / "hooks" / "validate-after-write.sh"


def _broken_vault(root: Path) -> Path:
    """Build a vault with broken wikilinks so verify_vault.py returns errors.

    Tests that expect verify to fire and block will see exit 2; tests that
    expect verify to be skipped will see exit 0 because the hook never runs
    verify in the first place.
    """
    for d in ["brain", "entities", "me", "inbox", "archive", "archive/inbox", "scratch"]:
        (root / d).mkdir(parents=True, exist_ok=True)
    (root / "brain" / "status.md").write_text(textwrap.dedent("""\
        ---
        updated: 2026-04-10
        ---
        # Status

        ## Today's Plan — 2026-04-10

        - [ ] Review PR [[does-not-exist]] [due:: 2026-04-10] [energy:: medium] [est:: 30min]
    """))
    (root / "brain" / "deadlines.md").write_text("# Deadlines\n")
    (root / "brain" / "goals.md").write_text("# Goals\n")
    (root / "me" / "profile.md").write_text("# Profile\n")
    (root / "_MANIFEST.md").write_text("# Manifest\n")
    return root


def _clean_vault(root: Path) -> Path:
    for d in ["brain", "entities", "me", "inbox", "archive", "archive/inbox", "scratch"]:
        (root / d).mkdir(parents=True, exist_ok=True)
    (root / "brain" / "status.md").write_text(textwrap.dedent("""\
        ---
        updated: 2026-04-10
        ---
        # Status

        ## Today's Plan — 2026-04-10

        - [ ] Review PR [due:: 2026-04-10] [energy:: medium] [est:: 30min]
    """))
    (root / "brain" / "deadlines.md").write_text("# Deadlines\n")
    (root / "brain" / "goals.md").write_text("# Goals\n")
    (root / "me" / "profile.md").write_text("# Profile\n")
    (root / "_MANIFEST.md").write_text("# Manifest\n")
    return root


@pytest.fixture
def broken_vault(tmp_path: Path) -> Iterator[Path]:
    yield _broken_vault(tmp_path / "cowork")


@pytest.fixture
def clean_vault(tmp_path: Path) -> Iterator[Path]:
    yield _clean_vault(tmp_path / "cowork")


def run_validate(payload: dict, vault_path: Path) -> tuple[int, str, str]:
    """Invoke validate-after-write.sh with the given tool payload."""
    env = os.environ.copy()
    env["VAULT_PATH"] = str(vault_path)
    result = subprocess.run(
        [str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    return result.returncode, result.stdout, result.stderr


def _verify_actually_fails_on(vault: Path) -> bool:
    """Sanity check: confirm verify_vault.py really fails on the broken fixture.

    If this returns False, our premise for distinguishing run-vs-skip is broken
    and the other tests don't actually prove anything.
    """
    script = HOOK.parent.parent / "scripts" / "verify_vault.py"
    result = subprocess.run(
        ["python3", str(script), str(vault),
         "--check", "wikilinks,entity-stubs,duplicates", "--json", "--quiet"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    try:
        data = json.loads(result.stdout)
        return data.get("summary", {}).get("errors", 0) > 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Sanity check — confirm the broken fixture really breaks verify
# ---------------------------------------------------------------------------

class TestFixtureSanity:
    def test_broken_vault_actually_breaks_verify(self, broken_vault: Path):
        assert _verify_actually_fails_on(broken_vault), (
            "broken_vault fixture must produce verify errors; otherwise the "
            "skip-vs-run tests below can't distinguish hook behavior"
        )

    def test_clean_vault_passes_verify(self, clean_vault: Path):
        assert not _verify_actually_fails_on(clean_vault), (
            "clean_vault fixture must pass verify"
        )


# ---------------------------------------------------------------------------
# MCP write path: always runs verify_vault.py
# ---------------------------------------------------------------------------

class TestMcpWritePath:
    def test_mcp_vault_create_runs_verify_clean(self, clean_vault: Path):
        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "mcp__obsidian__vault_create",
            "tool_input": {"path": "brain/new-note.md"},
        }
        code, _, _ = run_validate(payload, clean_vault)
        assert code == 0

    def test_mcp_vault_create_runs_verify_broken(self, broken_vault: Path):
        """Broken vault + MCP write → verify fires and blocks."""
        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "mcp__obsidian__vault_create",
            "tool_input": {"path": "brain/new-note.md"},
        }
        code, _, _ = run_validate(payload, broken_vault)
        assert code == 2

    def test_mcp_vault_update_runs_verify_broken(self, broken_vault: Path):
        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "mcp__obsidian__vault_update",
            "tool_input": {"path": "brain/status.md"},
        }
        code, _, _ = run_validate(payload, broken_vault)
        assert code == 2


# ---------------------------------------------------------------------------
# Bash path: only sanctioned vault-touching scripts trigger verify
# ---------------------------------------------------------------------------

class TestBashVaultTouchingScripts:
    """Bash invocations of vault-touching scripts should run verify_vault.py."""

    def test_archive_inbox_triggers_verify_clean(self, clean_vault: Path):
        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": f"python3 scripts/archive_inbox.py {clean_vault}"},
        }
        code, _, _ = run_validate(payload, clean_vault)
        assert code == 0

    def test_archive_inbox_triggers_verify_broken(self, broken_vault: Path):
        """Broken vault + sanctioned write-script → verify fires and blocks."""
        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": f"python3 scripts/archive_inbox.py {broken_vault}"},
        }
        code, _, _ = run_validate(payload, broken_vault)
        assert code == 2

    def test_update_hot_memory_triggers_verify_broken(self, broken_vault: Path):
        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {
                "command": f"python3 scripts/update_hot_memory.py {broken_vault}",
            },
        }
        code, _, _ = run_validate(payload, broken_vault)
        assert code == 2

    def test_verify_vault_triggers_verify_broken(self, broken_vault: Path):
        """Running verify after verify is a safe no-op but still surfaces failures."""
        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {
                "command": f"python3 scripts/verify_vault.py {broken_vault}",
            },
        }
        code, _, _ = run_validate(payload, broken_vault)
        assert code == 2

    def test_rebuild_manifest_triggers_verify_broken(self, broken_vault: Path):
        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {
                "command": f"python3 scripts/rebuild_manifest.py {broken_vault}",
            },
        }
        code, _, _ = run_validate(payload, broken_vault)
        assert code == 2


class TestBashNonVaultTouching:
    """Sanctioned-but-not-vault-touching scripts must skip verify.

    Verified against a broken vault: if the hook incorrectly ran verify, we'd
    see exit 2. Instead we must see exit 0 because verify was skipped.
    """

    def test_bump_version_skips_verify(self, broken_vault: Path):
        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "python3 scripts/bump_version.py --patch"},
        }
        code, _, _ = run_validate(payload, broken_vault)
        assert code == 0

    def test_setup_steps_skips_verify(self, broken_vault: Path):
        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "python3 scripts/setup_steps.py --foo"},
        }
        code, _, _ = run_validate(payload, broken_vault)
        assert code == 0

    def test_connect_mcp_client_skips_verify(self, broken_vault: Path):
        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "python3 scripts/connect_mcp_client.py"},
        }
        code, _, _ = run_validate(payload, broken_vault)
        assert code == 0


class TestBashUnrelatedCommands:
    """Bash commands that aren't in the sanctioned list must skip verify."""

    def test_ls_skips_verify(self, broken_vault: Path):
        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "ls -la"},
        }
        code, _, _ = run_validate(payload, broken_vault)
        assert code == 0

    def test_git_status_skips_verify(self, broken_vault: Path):
        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "git status"},
        }
        code, _, _ = run_validate(payload, broken_vault)
        assert code == 0

    def test_pytest_skips_verify(self, broken_vault: Path):
        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "python3 -m pytest tests/"},
        }
        code, _, _ = run_validate(payload, broken_vault)
        assert code == 0


# ---------------------------------------------------------------------------
# Malformed / missing input
# ---------------------------------------------------------------------------

class TestMalformed:
    def test_empty_stdin(self, clean_vault: Path):
        env = os.environ.copy()
        env["VAULT_PATH"] = str(clean_vault)
        result = subprocess.run(
            [str(HOOK)],
            input="",
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
        assert result.returncode == 0

    def test_invalid_json(self, clean_vault: Path):
        env = os.environ.copy()
        env["VAULT_PATH"] = str(clean_vault)
        result = subprocess.run(
            [str(HOOK)],
            input="not json",
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
        assert result.returncode == 0

    def test_missing_vault_dir_skips(self, tmp_path: Path):
        """If VAULT_PATH doesn't exist, skip verification silently."""
        env = os.environ.copy()
        env["VAULT_PATH"] = str(tmp_path / "does-not-exist")
        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "mcp__obsidian__vault_create",
            "tool_input": {"path": "brain/x.md"},
        }
        result = subprocess.run(
            [str(HOOK)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
        assert result.returncode == 0
