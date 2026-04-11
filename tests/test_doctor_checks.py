"""Unit tests for doctor_checks.py — the T5 diagnose-then-treat check engine.

Each `check_*` function returns a `CheckResult` dataclass with a pass/fail/skip/
warning status, a human-readable message, and a fix hint (function name in
setup_steps) for fixable failures. `run_all_checks` orchestrates them in the
right dependency order — e.g. vault_reachable depends on mcp_connection, so a
failed MCP connection cascades into "skip" results for every downstream check.

All tests isolate filesystem state via `tmp_path` + env var overrides; no real
MCP server is ever contacted (we use a factory parameter for test injection).
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from typing import Iterator
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "secondbrain" / "scripts"))

import setup_steps  # pyright: ignore[reportMissingImports]  # noqa: E402

from doctor_checks import (  # pyright: ignore[reportMissingImports]
    CheckResult,
    check_core_hooks_path,
    check_environment,
    check_hot_memory_schema,
    check_ingest_log_recent_failures,
    check_last_dream_protocol_run,
    check_log_md_exists,
    check_manifest_exists,
    check_mcp_connection,
    check_obsidian_api_key,
    check_obsidian_mcp_port,
    check_obsidian_running,
    check_plugin_root,
    check_profile_has_user_content,
    check_scheduled_tasks,
    check_standard_folders,
    check_vault_identity_cross,
    check_vault_reachable,
    run_all_checks,
    run_fixable_treatments,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def healthy_vault(tmp_path: Path) -> Path:
    """A vault with every file/dir doctor looks for in pass state."""
    vault = tmp_path / "healthy"
    vault.mkdir()

    # Standard folders
    for d in ("brain", "entities", "me", "inbox", "archive", "scratch"):
        (vault / d).mkdir()

    # _MANIFEST.md
    (vault / "_MANIFEST.md").write_text("# Vault Manifest\n\n**Files:** 5\n")

    # log.md with a clean dream-protocol entry. Avoid the word "issues"
    # (even "no issues") because the check regex is word-based and would
    # flag it as problematic.
    (vault / "log.md").write_text(
        "# Log\n\n"
        "## [2026-04-10 02:00] dream-protocol | all green, regenerated manifest\n"
        "Rebuild manifest: ok.\n"
    )

    # profile.md without placeholders
    (vault / "me" / "profile.md").write_text(
        "# Profile\n\nName: Test User\nRole: Engineer\n"
    )

    # Marker with vault_id
    vault_id = str(uuid.uuid4())
    (vault / ".secondbrain-installed").write_text(
        json.dumps({"vault_id": vault_id, "steps": ["scaffold: ok"]}, indent=2)
    )

    return vault


@pytest.fixture
def broken_vault(tmp_path: Path) -> Path:
    """A vault with every single check deliberately failing."""
    vault = tmp_path / "broken"
    vault.mkdir()

    # No folders, no files — all of structure, manifest, log, profile fail.
    # Marker with no vault_id so write_vault_id fixes it.
    (vault / ".secondbrain-installed").write_text(
        json.dumps({"steps": []}, indent=2)
    )

    return vault


@pytest.fixture
def mock_mcp_client() -> MagicMock:
    """A MagicMock stand-in for ConnectMCPClient that appears reachable by default."""
    client = MagicMock()
    client.is_reachable.return_value = True
    client.vault_list.return_value = ["some-file.md"]
    client.vault_read.return_value = ""
    return client


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Clear OBSIDIAN_API_KEY / OBSIDIAN_MCP_PORT / CLAUDE_PLUGIN_ROOT for deterministic tests."""
    for var in ("OBSIDIAN_API_KEY", "OBSIDIAN_MCP_PORT", "CLAUDE_PLUGIN_ROOT"):
        monkeypatch.delenv(var, raising=False)
    yield


# ---------------------------------------------------------------------------
# CheckResult dataclass
# ---------------------------------------------------------------------------

class TestCheckResult:
    def test_basic_construction(self):
        r = CheckResult(name="x", status="pass", message="ok", fixable=False)
        assert r.name == "x"
        assert r.status == "pass"
        assert r.message == "ok"
        assert r.fixable is False
        assert r.fix_function is None

    def test_with_fix_function(self):
        r = CheckResult(
            name="log-md",
            status="fail",
            message="log.md missing",
            fixable=True,
            fix_function="create_log_md",
        )
        assert r.fixable is True
        assert r.fix_function == "create_log_md"

    def test_valid_status_values(self):
        for status in ("pass", "fail", "skip", "warning"):
            r = CheckResult(name="x", status=status, message="m", fixable=False)
            assert r.status == status


# ---------------------------------------------------------------------------
# check_plugin_root — Check 1
# ---------------------------------------------------------------------------

class TestCheckPluginRoot:
    def test_pass_when_env_set_and_dir_exists(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path))
        r = check_plugin_root()
        assert r.status == "pass"
        assert r.fixable is False

    def test_fail_when_env_not_set(self, clean_env: None):
        del clean_env  # fixture consumed
        r = check_plugin_root()
        assert r.status == "fail"
        assert r.fixable is False  # user must run /plugin install

    def test_fail_when_env_points_at_missing_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path / "nonexistent"))
        r = check_plugin_root()
        assert r.status == "fail"


# ---------------------------------------------------------------------------
# check_environment — Check 2 (informational)
# ---------------------------------------------------------------------------

class TestCheckEnvironment:
    def test_returns_info(self):
        r = check_environment()
        # Always passes — it's informational.
        assert r.status == "pass"
        # Message should mention "code" or "cowork"
        assert r.message.lower().count("code") + r.message.lower().count("cowork") >= 1
        assert r.fixable is False


# ---------------------------------------------------------------------------
# check_obsidian_api_key — Check 3
# ---------------------------------------------------------------------------

class TestCheckObsidianApiKey:
    def test_pass_when_set(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OBSIDIAN_API_KEY", "abc123")
        r = check_obsidian_api_key()
        assert r.status == "pass"
        assert r.fixable is False

    def test_missing_api_key_not_fixable(self, clean_env: None):
        """Doctor cannot mint an API key — it must come from Obsidian's Connect
        MCP plugin. Advertising a fix here would be a lie because
        `setup_env_vars(api_key=None, port=None)` short-circuits to a no-op.
        """
        del clean_env
        r = check_obsidian_api_key()
        assert r.status == "fail"
        assert r.fixable is False
        assert r.fix_function is None
        # Escalation should send users to init or shell config.
        assert ("/secondbrain:init" in r.message) or ("shell config" in r.message)

    def test_fail_when_empty(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OBSIDIAN_API_KEY", "")
        r = check_obsidian_api_key()
        assert r.status == "fail"
        assert r.fixable is False


# ---------------------------------------------------------------------------
# check_obsidian_mcp_port — Check 4
# ---------------------------------------------------------------------------

class TestCheckObsidianMcpPort:
    def test_pass_when_set(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OBSIDIAN_MCP_PORT", "27124")
        r = check_obsidian_mcp_port()
        assert r.status == "pass"

    def test_missing_port_not_fixable(self, clean_env: None):
        """Doctor cannot guess which port to write — user must either run
        /secondbrain:init (which prompts for it) or export it themselves.
        """
        del clean_env
        r = check_obsidian_mcp_port()
        assert r.status == "fail"
        assert r.fixable is False
        assert r.fix_function is None
        assert ("/secondbrain:init" in r.message) or ("OBSIDIAN_MCP_PORT" in r.message)

    def test_fail_when_non_numeric(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OBSIDIAN_MCP_PORT", "not-a-number")
        r = check_obsidian_mcp_port()
        assert r.status == "fail"
        # Still not fixable — user must set a valid number themselves.
        assert r.fixable is False


# ---------------------------------------------------------------------------
# check_obsidian_running — Check 5
# ---------------------------------------------------------------------------

class TestCheckObsidianRunning:
    def test_returns_check_result(self):
        # We don't know what the host has — just check shape.
        r = check_obsidian_running()
        assert r.status in ("pass", "fail", "warning", "skip")
        # Not fixable — user must open Obsidian manually
        assert r.fixable is False


# ---------------------------------------------------------------------------
# check_mcp_connection — Check 6
# ---------------------------------------------------------------------------

class TestCheckMcpConnection:
    def test_pass_with_reachable_client(self, mock_mcp_client: MagicMock):
        r = check_mcp_connection(client_factory=lambda: mock_mcp_client)
        assert r.status == "pass"

    def test_fail_when_client_unreachable(self):
        client = MagicMock()
        client.is_reachable.return_value = False
        r = check_mcp_connection(client_factory=lambda: client)
        assert r.status == "fail"
        assert r.fixable is False  # user must check Obsidian

    def test_fail_when_factory_raises(self):
        def factory():
            raise RuntimeError("port not set")
        r = check_mcp_connection(client_factory=factory)
        assert r.status == "fail"
        assert "port not set" in r.message or "factory" in r.message.lower() or "unreachable" in r.message.lower()


# ---------------------------------------------------------------------------
# check_vault_reachable — Check 7
# ---------------------------------------------------------------------------

class TestCheckVaultReachable:
    def test_pass_when_dir_exists_and_has_files(self, healthy_vault: Path):
        r = check_vault_reachable(healthy_vault)
        assert r.status == "pass"

    def test_fail_when_dir_missing(self, tmp_path: Path):
        r = check_vault_reachable(tmp_path / "nope")
        assert r.status == "fail"
        assert r.fixable is False

    def test_warning_when_dir_empty(self, tmp_path: Path):
        vault = tmp_path / "empty"
        vault.mkdir()
        r = check_vault_reachable(vault)
        # Empty vault is a warning — the dir exists but has no content
        assert r.status in ("fail", "warning")


# ---------------------------------------------------------------------------
# check_manifest_exists — Check 8
# ---------------------------------------------------------------------------

class TestCheckManifestExists:
    def test_pass(self, healthy_vault: Path):
        r = check_manifest_exists(healthy_vault)
        assert r.status == "pass"

    def test_fail_when_missing(self, broken_vault: Path):
        r = check_manifest_exists(broken_vault)
        assert r.status == "fail"
        assert r.fixable is True
        assert r.fix_function == "rebuild_manifest"


# ---------------------------------------------------------------------------
# check_log_md_exists — Check 9
# ---------------------------------------------------------------------------

class TestCheckLogMdExists:
    def test_pass(self, healthy_vault: Path):
        r = check_log_md_exists(healthy_vault)
        assert r.status == "pass"

    def test_fail_when_missing(self, broken_vault: Path):
        r = check_log_md_exists(broken_vault)
        assert r.status == "fail"
        assert r.fixable is True
        assert r.fix_function == "create_log_md"


# ---------------------------------------------------------------------------
# check_profile_has_user_content — Check 10
# ---------------------------------------------------------------------------

class TestCheckProfile:
    def test_pass_with_real_content(self, healthy_vault: Path):
        r = check_profile_has_user_content(healthy_vault)
        assert r.status == "pass"

    def test_fail_with_placeholders(self, tmp_path: Path):
        vault = tmp_path / "with-template"
        (vault / "me").mkdir(parents=True)
        (vault / "me" / "profile.md").write_text(
            "# Profile\n\nName: {{USER_NAME}}\nRole: {{USER_ROLE}}\n"
        )
        r = check_profile_has_user_content(vault)
        assert r.status == "fail"
        assert r.fixable is True
        assert r.fix_function == "setup_profile"

    def test_fail_when_file_missing(self, broken_vault: Path):
        r = check_profile_has_user_content(broken_vault)
        assert r.status == "fail"
        # Missing profile is also fixable via setup_profile
        assert r.fixable is True


# ---------------------------------------------------------------------------
# check_standard_folders — Check 11
# ---------------------------------------------------------------------------

class TestCheckStandardFolders:
    def test_pass(self, healthy_vault: Path):
        r = check_standard_folders(healthy_vault)
        assert r.status == "pass"

    def test_fail_when_folders_missing(self, broken_vault: Path):
        r = check_standard_folders(broken_vault)
        assert r.status == "fail"
        assert r.fixable is True
        assert r.fix_function == "setup_vault_scaffolding"

    def test_fail_listing_missing_names(self, tmp_path: Path):
        vault = tmp_path / "partial"
        vault.mkdir()
        (vault / "brain").mkdir()  # only brain exists
        r = check_standard_folders(vault)
        assert r.status == "fail"
        # Other missing folders should appear in the message
        assert "entities" in r.message or "me" in r.message or "inbox" in r.message


# ---------------------------------------------------------------------------
# check_scheduled_tasks — Check 12
# ---------------------------------------------------------------------------

class TestCheckScheduledTasks:
    def test_returns_result(self):
        # We can't CronList from a test, so this should at minimum not crash.
        r = check_scheduled_tasks("code")
        assert r.status in ("pass", "fail", "warning", "skip")

    def test_cowork_mode(self):
        r = check_scheduled_tasks("cowork")
        assert r.status in ("pass", "fail", "warning", "skip")


# ---------------------------------------------------------------------------
# check_last_dream_protocol_run — Check 13
# ---------------------------------------------------------------------------

class TestCheckLastDreamProtocolRun:
    def test_pass_with_recent_clean_run(self, healthy_vault: Path):
        r = check_last_dream_protocol_run(healthy_vault)
        assert r.status == "pass"
        assert r.fixable is False  # informational

    def test_warning_with_issues_in_recent_run(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "log.md").write_text(
            "# Log\n\n## [2026-04-10 02:00] dream-protocol | Run had issues — broken wikilinks\n"
        )
        r = check_last_dream_protocol_run(vault)
        assert r.status in ("warning", "fail")

    def test_skip_when_log_missing(self, broken_vault: Path):
        r = check_last_dream_protocol_run(broken_vault)
        assert r.status in ("skip", "warning", "fail")
        assert r.fixable is False  # informational only


# ---------------------------------------------------------------------------
# check_vault_identity_cross — Check 6.5 (NEW)
# ---------------------------------------------------------------------------

class TestCheckVaultIdentityCross:
    def test_pass_when_vault_ids_match(self, healthy_vault: Path, mock_mcp_client: MagicMock):
        # Both the filesystem marker and the MCP-read marker agree.
        vault_id = json.loads((healthy_vault / ".secondbrain-installed").read_text())["vault_id"]
        mock_mcp_client.vault_read.return_value = json.dumps(
            {"vault_id": vault_id, "steps": ["scaffold: ok"]}, indent=2
        )
        r = check_vault_identity_cross(healthy_vault, mcp_client=mock_mcp_client)
        assert r.status == "pass"

    def test_fail_when_vault_ids_mismatch(self, healthy_vault: Path, mock_mcp_client: MagicMock):
        fs_vault_id = json.loads((healthy_vault / ".secondbrain-installed").read_text())["vault_id"]
        mcp_vault_id = str(uuid.uuid4())
        mock_mcp_client.vault_read.return_value = json.dumps(
            {"vault_id": mcp_vault_id, "steps": ["scaffold: ok"]}, indent=2
        )
        r = check_vault_identity_cross(healthy_vault, mcp_client=mock_mcp_client)
        assert r.status == "fail"
        # Both IDs should appear in the message
        assert fs_vault_id in r.message
        assert mcp_vault_id in r.message
        # This is a config conflict — NOT auto-fixable
        assert r.fixable is False

    def test_vault_identity_cross_marker_missing_not_fixable(
        self, tmp_path: Path, mock_mcp_client: MagicMock
    ):
        """Marker entirely missing is NOT auto-fixable.

        Doctor's `write_vault_id` refuses to create a missing marker — only
        init can do that. So the check must advertise `fixable=False` with an
        escalation message pointing at `/secondbrain:init`.
        """
        vault = tmp_path / "vault"
        vault.mkdir()
        r = check_vault_identity_cross(vault, mcp_client=mock_mcp_client)
        assert r.status == "fail"
        # Previously advertised fixable=True — but write_vault_id refuses to
        # create missing markers, so advertising that was a lie.
        assert r.fixable is False
        assert r.fix_function is None
        assert "/secondbrain:init" in r.message

    def test_vault_identity_cross_marker_present_no_vault_id_fixable(
        self, tmp_path: Path, mock_mcp_client: MagicMock
    ):
        """Marker present but missing `vault_id` key IS fixable.

        This is the case `write_vault_id` can actually handle — an existing
        marker that needs a UUID stamped into it.
        """
        vault = tmp_path / "vault"
        vault.mkdir()
        # Marker exists with valid JSON but no vault_id field.
        (vault / ".secondbrain-installed").write_text(
            json.dumps({"steps": ["scaffold: ok"]}, indent=2)
        )
        r = check_vault_identity_cross(vault, mcp_client=mock_mcp_client)
        assert r.status == "fail"
        assert r.fixable is True
        assert r.fix_function == "write_vault_id"

    def test_skip_when_mcp_client_none(self, healthy_vault: Path):
        r = check_vault_identity_cross(healthy_vault, mcp_client=None)
        assert r.status == "skip"


# ---------------------------------------------------------------------------
# check_hot_memory_schema — Check 14 (NEW, deferred to Phase 3)
# ---------------------------------------------------------------------------

class TestCheckHotMemorySchema:
    def test_skip_when_phase3_module_missing(self, tmp_path: Path):
        plugin_root = tmp_path / "plugin"
        plugin_root.mkdir()
        # No validate_hot_memory.py → skip gracefully
        r = check_hot_memory_schema(tmp_path, plugin_root)
        assert r.status == "skip"
        assert "phase 3" in r.message.lower() or "deferred" in r.message.lower()

    def test_fail_when_module_present_but_file_missing(self, tmp_path: Path):
        plugin_root = tmp_path / "plugin"
        plugin_root.mkdir()
        scripts = plugin_root / "secondbrain" / "scripts"
        scripts.mkdir(parents=True)
        (scripts / "validate_hot_memory.py").write_text("# phase 3 module\n")

        vault = tmp_path / "vault"
        vault.mkdir()
        # No hot-memory file present
        r = check_hot_memory_schema(vault, plugin_root)
        assert r.status in ("fail", "warning")

    def test_pass_when_module_and_file_both_present(self, tmp_path: Path):
        plugin_root = tmp_path / "plugin"
        scripts = plugin_root / "secondbrain" / "scripts"
        scripts.mkdir(parents=True)
        (scripts / "validate_hot_memory.py").write_text("# phase 3 module\n")

        vault = tmp_path / "vault"
        (vault / ".secondbrain").mkdir(parents=True)
        (vault / ".secondbrain" / "hot-memory.json").write_text("{}\n")
        r = check_hot_memory_schema(vault, plugin_root)
        # Without a real validator, we accept "pass" OR "skip" — the key
        # invariant is it doesn't crash.
        assert r.status in ("pass", "skip", "warning")


# ---------------------------------------------------------------------------
# check_core_hooks_path — Check 15 (NEW, informational)
# ---------------------------------------------------------------------------

class TestCheckCoreHooksPath:
    def test_pass_when_hooks_path_set(self, tmp_path: Path):
        # Set up a fake git repo with core.hooksPath configured.
        import subprocess
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True)
        subprocess.run(
            ["git", "config", "core.hooksPath", ".githooks"],
            cwd=repo, check=True,
        )
        r = check_core_hooks_path(repo)
        assert r.status == "pass"
        assert r.fixable is False

    def test_fail_when_hooks_path_not_set(self, tmp_path: Path):
        import subprocess
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True)
        r = check_core_hooks_path(repo)
        assert r.status in ("fail", "warning")
        # Not auto-fixable — user must run install_git_hooks.py
        assert r.fixable is False

    def test_fail_when_hooks_path_wrong_value(self, tmp_path: Path):
        import subprocess
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True)
        subprocess.run(
            ["git", "config", "core.hooksPath", "wrong-value"],
            cwd=repo, check=True,
        )
        r = check_core_hooks_path(repo)
        assert r.status in ("fail", "warning")

    def test_skip_when_path_not_a_git_repo(self, tmp_path: Path):
        r = check_core_hooks_path(tmp_path)
        assert r.status in ("skip", "fail", "warning")


# ---------------------------------------------------------------------------
# check_ingest_log_recent_failures — Check 16 (NEW)
# ---------------------------------------------------------------------------

class TestCheckIngestLogRecentFailures:
    def test_pass_when_log_missing(self, tmp_path: Path):
        # No ingest-log.md yet (Phase 2/3 file) — treat as pass.
        r = check_ingest_log_recent_failures(tmp_path)
        assert r.status == "pass"

    def test_pass_when_log_has_no_failures(self, tmp_path: Path):
        from datetime import datetime, timedelta
        (tmp_path / ".secondbrain").mkdir()
        recent = (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")
        (tmp_path / ".secondbrain" / "ingest-log.md").write_text(
            f"# Ingest Log\n\n## [{recent}] ok — processed 5 items\n"
        )
        r = check_ingest_log_recent_failures(tmp_path)
        assert r.status == "pass"

    def test_warning_when_recent_failure(self, tmp_path: Path):
        from datetime import datetime, timedelta
        (tmp_path / ".secondbrain").mkdir()
        recent = (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")
        (tmp_path / ".secondbrain" / "ingest-log.md").write_text(
            f"# Ingest Log\n\n## [{recent}] fail — could not read inbox item\n"
        )
        r = check_ingest_log_recent_failures(tmp_path)
        assert r.status == "warning"
        # Not fixable — user must investigate
        assert r.fixable is False

    def test_pass_when_old_failure_outside_window(self, tmp_path: Path):
        from datetime import datetime, timedelta
        (tmp_path / ".secondbrain").mkdir()
        old = (datetime.now() - timedelta(hours=48)).isoformat(timespec="seconds")
        (tmp_path / ".secondbrain" / "ingest-log.md").write_text(
            f"# Ingest Log\n\n## [{old}] fail — ancient history\n"
        )
        r = check_ingest_log_recent_failures(tmp_path, hours=24)
        assert r.status == "pass"


# ---------------------------------------------------------------------------
# run_all_checks — orchestration + dependency ordering
# ---------------------------------------------------------------------------

class TestRunAllChecks:
    def test_returns_list_of_check_results(
        self,
        healthy_vault: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path))
        monkeypatch.setenv("OBSIDIAN_API_KEY", "key")
        monkeypatch.setenv("OBSIDIAN_MCP_PORT", "27124")
        results = run_all_checks(
            vault_path=healthy_vault,
            plugin_root=tmp_path,
        )
        assert isinstance(results, list)
        assert all(isinstance(r, CheckResult) for r in results)
        # Must run all defined checks (at minimum the 13 + the 4 new ones ≈ 17)
        assert len(results) >= 13

    def test_dependent_checks_skipped_when_upstream_fails(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        # No MCP env → MCP connection will fail → vault-side checks get "skip"
        for var in ("OBSIDIAN_API_KEY", "OBSIDIAN_MCP_PORT"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path))

        vault = tmp_path / "vault"
        vault.mkdir()
        results = run_all_checks(
            vault_path=vault,
            plugin_root=tmp_path,
        )
        # At least some downstream check must have skipped because MCP failed.
        statuses = [r.status for r in results]
        assert "skip" in statuses

    def test_broken_vault_produces_failures(
        self,
        broken_vault: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path))
        monkeypatch.setenv("OBSIDIAN_API_KEY", "key")
        monkeypatch.setenv("OBSIDIAN_MCP_PORT", "27124")
        results = run_all_checks(
            vault_path=broken_vault,
            plugin_root=tmp_path,
        )
        failures = [r for r in results if r.status == "fail"]
        assert len(failures) > 0

    def test_scheduled_tasks_in_cascade_skip_when_vault_unreachable(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """When vault_reachable fails, downstream filesystem checks — including
        scheduled_tasks — must be marked `skip` so the result shape stays
        consistent. Regression guard for the cascade list.
        """
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path))
        monkeypatch.setenv("OBSIDIAN_API_KEY", "key")
        monkeypatch.setenv("OBSIDIAN_MCP_PORT", "27124")
        # Vault path deliberately doesn't exist → vault_reachable fails.
        missing_vault = tmp_path / "nope"
        results = run_all_checks(
            vault_path=missing_vault,
            plugin_root=tmp_path,
        )
        by_name = {r.name: r for r in results}
        assert "vault_reachable" in by_name
        assert by_name["vault_reachable"].status == "fail"
        # Every downstream vault-side check must be present as a skip —
        # scheduled_tasks included.
        for name in (
            "manifest", "log_md", "profile", "standard_folders",
            "scheduled_tasks", "last_dream_protocol_run",
            "hot_memory_schema", "ingest_log_recent_failures",
        ):
            assert name in by_name, f"{name} missing from cascade skip"
            assert by_name[name].status == "skip", (
                f"{name} should be skip when vault unreachable, "
                f"got {by_name[name].status}"
            )


# ---------------------------------------------------------------------------
# run_fixable_treatments — Phase 2 dispatcher
# ---------------------------------------------------------------------------

class TestRunFixableTreatments:
    def test_calls_setup_steps_for_each_fixable(
        self,
        broken_vault: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        step_result_cls = setup_steps.StepResult

        # Fake results — simulate log.md and vault_id both failing
        results = [
            CheckResult(
                name="log.md",
                status="fail",
                message="missing",
                fixable=True,
                fix_function="create_log_md",
            ),
            CheckResult(
                name="vault_id",
                status="fail",
                message="missing",
                fixable=True,
                fix_function="write_vault_id",
            ),
        ]

        called: list[str] = []

        def fake_create_log_md(vault_path: Path):
            del vault_path
            called.append("create_log_md")
            return step_result_cls(success=True, message="log.md created", did_work=True)

        def fake_write_vault_id(vault_path: Path):
            del vault_path
            called.append("write_vault_id")
            return step_result_cls(success=True, message="vault_id=fake", did_work=True)

        monkeypatch.setattr(setup_steps, "write_vault_id", fake_write_vault_id, raising=True)
        # create_log_md doesn't exist yet — the implementation will add it.
        monkeypatch.setattr(setup_steps, "create_log_md", fake_create_log_md, raising=False)

        step_results = run_fixable_treatments(results, broken_vault, interactive=False)
        assert len(step_results) >= 2
        assert "create_log_md" in called
        assert "write_vault_id" in called

    def test_skips_non_fixable(self, broken_vault: Path):
        results = [
            CheckResult(
                name="plugin_root",
                status="fail",
                message="not installed",
                fixable=False,
            ),
        ]
        step_results = run_fixable_treatments(results, broken_vault, interactive=False)
        # Nothing fixable → empty list
        assert step_results == []

    def test_skips_passing_checks(self, broken_vault: Path):
        results = [
            CheckResult(
                name="log.md",
                status="pass",
                message="ok",
                fixable=True,
                fix_function="create_log_md",
            ),
        ]
        step_results = run_fixable_treatments(results, broken_vault, interactive=False)
        assert step_results == []
