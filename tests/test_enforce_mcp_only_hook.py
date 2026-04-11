"""Tests for enforce-mcp-only.sh — PreToolUse hook that blocks direct Edit/Write/Bash
writes to registered vault paths so the vault can only be mutated through MCP or
through sanctioned Python scripts.

Layout:

    Edit/Write/NotebookEdit
        - Pre-init (no vaults.json)           → allow
        - Vault path, registered              → block
        - Non-vault path                      → allow
        - vaults.json itself                  → block
    Bash
        - Sanctioned script invocations       → allow
        - Pure read commands                  → allow
        - Writes to vault via mv/rm/cp/redir  → block
        - Writes to vaults.json               → block
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Iterator

import pytest

HOOK = Path(__file__).resolve().parent.parent / "secondbrain" / "hooks" / "enforce-mcp-only.sh"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_hook_edit(
    tool_name: str,
    file_path: str,
    vaults_config_path: Path | None = None,
) -> tuple[int, str, str]:
    """Invoke the hook for an Edit/Write/NotebookEdit call."""
    payload = json.dumps({
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": {"file_path": file_path},
    })
    env = {}
    if vaults_config_path is not None:
        env["SECONDBRAIN_VAULTS_CONFIG"] = str(vaults_config_path)
    # Preserve PATH so python3 is findable.
    import os
    full_env = os.environ.copy()
    full_env.update(env)
    result = subprocess.run(
        [str(HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=10,
        env=full_env,
    )
    return result.returncode, result.stdout, result.stderr


def run_hook_bash(
    command: str,
    vaults_config_path: Path | None = None,
) -> tuple[int, str, str]:
    """Invoke the hook for a Bash tool call."""
    payload = json.dumps({
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
    })
    import os
    full_env = os.environ.copy()
    if vaults_config_path is not None:
        full_env["SECONDBRAIN_VAULTS_CONFIG"] = str(vaults_config_path)
    result = subprocess.run(
        [str(HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=10,
        env=full_env,
    )
    return result.returncode, result.stdout, result.stderr


def write_vaults_config(config_path: Path, vault_paths: list[Path]) -> None:
    """Write a minimal vaults.json containing the given registered vault paths."""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    entries = []
    for i, vp in enumerate(vault_paths):
        entries.append({
            "id": f"id-{i}",
            "path": str(vp),
            "name": f"vault-{i}",
            "role": "personal",
            "added_at": "2026-04-10T00:00:00",
            "with_push": False,
        })
    data = {
        "schema_version": 1,
        "vaults": entries,
        "active_vault_id": entries[0]["id"] if entries else None,
    }
    config_path.write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_env(tmp_path: Path) -> Iterator[tuple[Path, Path]]:
    """Give each test an isolated vault + vaults.json config path.

    Yields (vault_dir, vaults_config_path).
    """
    vault = tmp_path / "cowork"
    vault.mkdir()
    (vault / "brain").mkdir()
    (vault / "entities").mkdir()
    (vault / "inbox").mkdir()
    (vault / "archive").mkdir()

    config_path = tmp_path / "config" / "secondbrain" / "vaults.json"
    yield vault, config_path


# ---------------------------------------------------------------------------
# Pre-init: vaults.json doesn't exist yet → allow everything (fail-open)
# ---------------------------------------------------------------------------

class TestPreInit:
    def test_edit_on_vault_shaped_path_allowed_preinit(self, isolated_env: tuple[Path, Path]):
        vault, config = isolated_env
        # Config file does NOT exist.
        code, _, _ = run_hook_edit(
            "Edit",
            str(vault / "brain" / "status.md"),
            vaults_config_path=config,
        )
        assert code == 0

    def test_write_on_vault_shaped_path_allowed_preinit(self, isolated_env: tuple[Path, Path]):
        vault, config = isolated_env
        code, _, _ = run_hook_edit(
            "Write",
            str(vault / "brain" / "new.md"),
            vaults_config_path=config,
        )
        assert code == 0

    def test_bash_write_to_vault_allowed_preinit(self, isolated_env: tuple[Path, Path]):
        vault, config = isolated_env
        code, _, _ = run_hook_bash(
            f"rm {vault}/brain/status.md",
            vaults_config_path=config,
        )
        assert code == 0

    def test_edit_vaults_json_allowed_preinit(self, isolated_env: tuple[Path, Path]):
        _, config = isolated_env
        code, _, _ = run_hook_edit(
            "Edit",
            str(config),
            vaults_config_path=config,
        )
        assert code == 0


# ---------------------------------------------------------------------------
# Post-init: Edit / Write / NotebookEdit blocks
# ---------------------------------------------------------------------------

class TestEditWriteBlocks:
    def test_edit_on_vault_path_blocked(self, isolated_env: tuple[Path, Path]):
        vault, config = isolated_env
        write_vaults_config(config, [vault])
        code, _, stderr = run_hook_edit(
            "Edit",
            str(vault / "brain" / "status.md"),
            vaults_config_path=config,
        )
        assert code == 2
        assert "BLOCKED" in stderr
        assert "mcp__obsidian__vault_" in stderr

    def test_write_on_vault_path_blocked(self, isolated_env: tuple[Path, Path]):
        vault, config = isolated_env
        write_vaults_config(config, [vault])
        code, _, stderr = run_hook_edit(
            "Write",
            str(vault / "entities" / "alice.md"),
            vaults_config_path=config,
        )
        assert code == 2
        assert "BLOCKED" in stderr

    def test_notebook_edit_on_vault_path_blocked(self, isolated_env: tuple[Path, Path]):
        vault, config = isolated_env
        write_vaults_config(config, [vault])
        code, _, stderr = run_hook_edit(
            "NotebookEdit",
            str(vault / "brain" / "notebook.ipynb"),
            vaults_config_path=config,
        )
        assert code == 2
        assert "BLOCKED" in stderr

    def test_edit_on_nested_vault_path_blocked(self, isolated_env: tuple[Path, Path]):
        vault, config = isolated_env
        write_vaults_config(config, [vault])
        code, _, _ = run_hook_edit(
            "Edit",
            str(vault / "archive" / "inbox" / "2026-04" / "old.md"),
            vaults_config_path=config,
        )
        assert code == 2

    def test_edit_on_non_vault_path_allowed(self, tmp_path: Path, isolated_env: tuple[Path, Path]):
        vault, config = isolated_env
        write_vaults_config(config, [vault])
        # Path outside the vault.
        outside = tmp_path / "unrelated" / "file.py"
        code, _, _ = run_hook_edit(
            "Edit",
            str(outside),
            vaults_config_path=config,
        )
        assert code == 0

    def test_edit_on_vaults_json_blocked(self, isolated_env: tuple[Path, Path]):
        vault, config = isolated_env
        write_vaults_config(config, [vault])
        code, _, stderr = run_hook_edit(
            "Edit",
            str(config),
            vaults_config_path=config,
        )
        assert code == 2
        assert "vaults.json" in stderr or "BLOCKED" in stderr

    def test_write_on_vaults_json_blocked(self, isolated_env: tuple[Path, Path]):
        vault, config = isolated_env
        write_vaults_config(config, [vault])
        code, _, _ = run_hook_edit(
            "Write",
            str(config),
            vaults_config_path=config,
        )
        assert code == 2

    def test_missing_file_path_allows(self, isolated_env: tuple[Path, Path]):
        """If tool_input has no file_path, we can't enforce anything — allow."""
        _, config = isolated_env
        # Post-init to ensure the protection codepath is exercised.
        vault = isolated_env[0]
        write_vaults_config(config, [vault])
        payload = json.dumps({
            "hook_event_name": "PreToolUse",
            "tool_name": "Edit",
            "tool_input": {},
        })
        import os
        full_env = os.environ.copy()
        full_env["SECONDBRAIN_VAULTS_CONFIG"] = str(config)
        result = subprocess.run(
            [str(HOOK)],
            input=payload,
            capture_output=True,
            text=True,
            timeout=10,
            env=full_env,
        )
        assert result.returncode == 0

    def test_tilde_expansion(self, isolated_env: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch):
        """Paths starting with ~ should be resolved and matched against vault paths."""
        vault, config = isolated_env
        write_vaults_config(config, [vault])
        # Pretend HOME points at vault's parent so ~/cowork resolves to vault.
        monkeypatch.setenv("HOME", str(vault.parent))
        code, _, _ = run_hook_edit(
            "Edit",
            "~/cowork/brain/status.md",
            vaults_config_path=config,
        )
        assert code == 2


# ---------------------------------------------------------------------------
# Post-init: Bash enforcement
# ---------------------------------------------------------------------------

class TestBashSanctionedScripts:
    """Sanctioned script invocations should always be allowed."""

    def test_verify_vault_allowed(self, isolated_env: tuple[Path, Path]):
        vault, config = isolated_env
        write_vaults_config(config, [vault])
        code, _, _ = run_hook_bash(
            f"python3 scripts/verify_vault.py {vault}",
            vaults_config_path=config,
        )
        assert code == 0

    def test_archive_inbox_allowed(self, isolated_env: tuple[Path, Path]):
        vault, config = isolated_env
        write_vaults_config(config, [vault])
        code, _, _ = run_hook_bash(
            f"python3 scripts/archive_inbox.py {vault}",
            vaults_config_path=config,
        )
        assert code == 0

    def test_update_hot_memory_allowed(self, isolated_env: tuple[Path, Path]):
        vault, config = isolated_env
        write_vaults_config(config, [vault])
        code, _, _ = run_hook_bash(
            f"python3 ${{CLAUDE_PLUGIN_ROOT}}/scripts/update_hot_memory.py {vault}",
            vaults_config_path=config,
        )
        assert code == 0

    def test_migrate_v2_to_v3_allowed(self, isolated_env: tuple[Path, Path]):
        vault, config = isolated_env
        write_vaults_config(config, [vault])
        code, _, _ = run_hook_bash(
            f"python3 scripts/migrate_v2_to_v3.py {vault}",
            vaults_config_path=config,
        )
        assert code == 0


class TestBashReadOnly:
    """Read-only commands pointed at the vault should be allowed."""

    def test_cat_vault_file_allowed(self, isolated_env: tuple[Path, Path]):
        vault, config = isolated_env
        write_vaults_config(config, [vault])
        code, _, _ = run_hook_bash(
            f"cat {vault}/brain/status.md",
            vaults_config_path=config,
        )
        assert code == 0

    def test_ls_vault_allowed(self, isolated_env: tuple[Path, Path]):
        vault, config = isolated_env
        write_vaults_config(config, [vault])
        code, _, _ = run_hook_bash(
            f"ls {vault}/brain/",
            vaults_config_path=config,
        )
        assert code == 0

    def test_grep_vault_allowed(self, isolated_env: tuple[Path, Path]):
        vault, config = isolated_env
        write_vaults_config(config, [vault])
        code, _, _ = run_hook_bash(
            f"grep -r 'pattern' {vault}/brain/",
            vaults_config_path=config,
        )
        assert code == 0

    def test_head_tail_allowed(self, isolated_env: tuple[Path, Path]):
        vault, config = isolated_env
        write_vaults_config(config, [vault])
        code, _, _ = run_hook_bash(
            f"head -20 {vault}/brain/status.md",
            vaults_config_path=config,
        )
        assert code == 0

    def test_cat_vaults_json_allowed(self, isolated_env: tuple[Path, Path]):
        vault, config = isolated_env
        write_vaults_config(config, [vault])
        code, _, _ = run_hook_bash(
            f"cat {config}",
            vaults_config_path=config,
        )
        assert code == 0

    def test_unrelated_command_allowed(self, isolated_env: tuple[Path, Path]):
        vault, config = isolated_env
        write_vaults_config(config, [vault])
        code, _, _ = run_hook_bash(
            "git status",
            vaults_config_path=config,
        )
        assert code == 0


class TestBashWriteBlocks:
    """Writes to the vault via Bash should be blocked."""

    def test_redirection_to_vault_blocked(self, isolated_env: tuple[Path, Path]):
        vault, config = isolated_env
        write_vaults_config(config, [vault])
        code, _, stderr = run_hook_bash(
            f"cat foo.md > {vault}/brain/status.md",
            vaults_config_path=config,
        )
        assert code == 2
        assert "BLOCKED" in stderr

    def test_append_to_vault_blocked(self, isolated_env: tuple[Path, Path]):
        vault, config = isolated_env
        write_vaults_config(config, [vault])
        code, _, _ = run_hook_bash(
            f"echo 'x' >> {vault}/brain/status.md",
            vaults_config_path=config,
        )
        assert code == 2

    def test_sed_i_on_vault_blocked(self, isolated_env: tuple[Path, Path]):
        vault, config = isolated_env
        write_vaults_config(config, [vault])
        code, _, _ = run_hook_bash(
            f"sed -i 's/old/new/' {vault}/brain/status.md",
            vaults_config_path=config,
        )
        assert code == 2

    def test_rm_on_vault_blocked(self, isolated_env: tuple[Path, Path]):
        vault, config = isolated_env
        write_vaults_config(config, [vault])
        code, _, _ = run_hook_bash(
            f"rm {vault}/brain/status.md",
            vaults_config_path=config,
        )
        assert code == 2

    def test_mv_into_vault_blocked(self, isolated_env: tuple[Path, Path]):
        vault, config = isolated_env
        write_vaults_config(config, [vault])
        code, _, _ = run_hook_bash(
            f"mv /tmp/foo.md {vault}/brain/",
            vaults_config_path=config,
        )
        assert code == 2

    def test_cp_into_vault_blocked(self, isolated_env: tuple[Path, Path]):
        vault, config = isolated_env
        write_vaults_config(config, [vault])
        code, _, _ = run_hook_bash(
            f"cp /tmp/foo.md {vault}/brain/status.md",
            vaults_config_path=config,
        )
        assert code == 2

    def test_touch_in_vault_blocked(self, isolated_env: tuple[Path, Path]):
        vault, config = isolated_env
        write_vaults_config(config, [vault])
        code, _, _ = run_hook_bash(
            f"touch {vault}/brain/new.md",
            vaults_config_path=config,
        )
        assert code == 2

    def test_bash_edit_vaults_json_blocked(self, isolated_env: tuple[Path, Path]):
        vault, config = isolated_env
        write_vaults_config(config, [vault])
        code, _, stderr = run_hook_bash(
            f"echo '{{}}' > {config}",
            vaults_config_path=config,
        )
        assert code == 2
        assert "BLOCKED" in stderr

    def test_write_to_non_vault_allowed(self, tmp_path: Path, isolated_env: tuple[Path, Path]):
        vault, config = isolated_env
        write_vaults_config(config, [vault])
        other = tmp_path / "unrelated"
        other.mkdir()
        code, _, _ = run_hook_bash(
            f"echo 'x' > {other}/file.txt",
            vaults_config_path=config,
        )
        assert code == 0


# ---------------------------------------------------------------------------
# Non-Edit/Write/Bash tool names pass through
# ---------------------------------------------------------------------------

class TestOtherTools:
    def test_mcp_tool_ignored(self, isolated_env: tuple[Path, Path]):
        vault, config = isolated_env
        write_vaults_config(config, [vault])
        # MCP vault_create isn't our concern here — the other hook handles it.
        payload = json.dumps({
            "hook_event_name": "PreToolUse",
            "tool_name": "mcp__obsidian__vault_create",
            "tool_input": {"path": "brain/status.md"},
        })
        import os
        full_env = os.environ.copy()
        full_env["SECONDBRAIN_VAULTS_CONFIG"] = str(config)
        result = subprocess.run(
            [str(HOOK)],
            input=payload,
            capture_output=True,
            text=True,
            timeout=10,
            env=full_env,
        )
        assert result.returncode == 0

    def test_unknown_tool_ignored(self, isolated_env: tuple[Path, Path]):
        vault, config = isolated_env
        write_vaults_config(config, [vault])
        payload = json.dumps({
            "hook_event_name": "PreToolUse",
            "tool_name": "Something",
            "tool_input": {"file_path": str(vault / "brain" / "x.md")},
        })
        import os
        full_env = os.environ.copy()
        full_env["SECONDBRAIN_VAULTS_CONFIG"] = str(config)
        result = subprocess.run(
            [str(HOOK)],
            input=payload,
            capture_output=True,
            text=True,
            timeout=10,
            env=full_env,
        )
        assert result.returncode == 0


# ---------------------------------------------------------------------------
# Malformed input — fail open
# ---------------------------------------------------------------------------

class TestMalformed:
    def test_empty_stdin(self):
        result = subprocess.run(
            [str(HOOK)],
            input="",
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0

    def test_invalid_json(self):
        result = subprocess.run(
            [str(HOOK)],
            input="not json",
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0

    def test_missing_tool_name(self):
        payload = json.dumps({"hook_event_name": "PreToolUse", "tool_input": {}})
        result = subprocess.run(
            [str(HOOK)],
            input=payload,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0


# ---------------------------------------------------------------------------
# Security bypass regression tests (T4 follow-up)
# ---------------------------------------------------------------------------

class TestSecurityBypasses:
    """Regression tests for bypasses flagged in T4 code review.

    These tests intentionally use tempfile.mkdtemp() for Issue 1 so pytest's
    tmp_path fixture (which pre-resolves on macOS) can't mask the bypass.
    """

    def test_bash_bypass_via_macos_symlink_prefix(self):
        """Issue 1: macOS /tmp → /private/tmp or /var/folders → /private/var/folders
        substring mismatch lets a Bash write sneak past the vault check.

        If the vault is registered with an unresolved path (as the agent wrote
        it in vaults.json) but the hook only searches for the .resolve()'d form,
        the command's literal path never matches.
        """
        import tempfile
        import os

        raw_root = tempfile.mkdtemp(prefix="sb_symlink_test_")
        try:
            vault = Path(raw_root) / "cowork"
            vault.mkdir()
            (vault / "brain").mkdir()

            config_path = Path(raw_root) / "config" / "secondbrain" / "vaults.json"
            write_vaults_config(config_path, [vault])

            # Agent writes command using the unresolved path (as provided by env).
            # If raw_root was '/var/folders/...', the resolved form is '/private/var/folders/...'.
            # The hook must catch both.
            command = f"rm {vault}/brain/status.md"
            code, _, _ = run_hook_bash(command, vaults_config_path=config_path)
            assert code == 2, f"expected block for {command}; got {code}"
        finally:
            import shutil
            shutil.rmtree(raw_root, ignore_errors=True)

    def test_echo_literal_script_name_not_sanctioned(
        self, isolated_env: tuple[Path, Path]
    ):
        """Issue 2: `echo archive_inbox.py > /vault/x.md` should NOT be treated
        as a sanctioned-script invocation. The current substring check lets the
        write sneak through because the string 'archive_inbox.py' appears in
        the command.
        """
        vault, config = isolated_env
        write_vaults_config(config, [vault])
        command = f"echo archive_inbox.py > {vault}/brain/status.md"
        code, _, _ = run_hook_bash(command, vaults_config_path=config)
        assert code == 2, f"expected block for {command}; got {code}"

    def test_chained_sanctioned_script_after_vault_write_not_sanctioned(
        self, isolated_env: tuple[Path, Path]
    ):
        """Issue 2: `rm /vault/x.md && python3 verify_vault.py` should NOT be
        allowed. The rm is the primary action; the trailing sanctioned call
        shouldn't grant it a free pass.
        """
        vault, config = isolated_env
        write_vaults_config(config, [vault])
        command = f"rm {vault}/brain/status.md && python3 verify_vault.py"
        code, _, _ = run_hook_bash(command, vaults_config_path=config)
        assert code == 2, f"expected block for {command}; got {code}"

    def test_sed_i_variants_blocked(self, isolated_env: tuple[Path, Path]):
        """Issue 3: `sed -i` regex misses common variants:
          - `sed -i.bak` (GNU backup suffix)
          - `sed -iE` (combined flags)
          - `sed -i ''` (macOS BSD sed idiom, requires empty-string arg)
        """
        vault, config = isolated_env
        write_vaults_config(config, [vault])

        variants = [
            f"sed -i.bak 's/old/new/' {vault}/brain/status.md",
            f"sed -iE 's/old/new/' {vault}/brain/status.md",
            f"sed -i '' 's/old/new/' {vault}/brain/status.md",
        ]
        for command in variants:
            code, _, _ = run_hook_bash(command, vaults_config_path=config)
            assert code == 2, f"expected block for {command}; got {code}"

    def test_read_vault_redirect_to_non_vault_allowed(
        self, tmp_path: Path, isolated_env: tuple[Path, Path]
    ):
        """Issue 4: `cat /vault/brain/status.md > /tmp/backup.md` is a legitimate
        read-from-vault-write-elsewhere. It must be allowed.

        (This test currently passes because Issue 1's bug hides the problem:
        the command references a vault path but the hook's path matching is
        broken. After Issue 1 is fixed this will start failing without the
        Issue 4 fix.)
        """
        vault, config = isolated_env
        write_vaults_config(config, [vault])
        outside = tmp_path / "backup.md"
        command = f"cat {vault}/brain/status.md > {outside}"
        code, _, _ = run_hook_bash(command, vaults_config_path=config)
        assert code == 0, f"expected allow for {command}; got {code}"
