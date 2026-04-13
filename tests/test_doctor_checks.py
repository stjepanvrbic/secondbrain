"""Unit tests for doctor_checks.py — the T5 diagnose-then-treat check engine.

Each `check_*` function returns a `CheckResult` dataclass with a pass/fail/
warning status, a human-readable message, and a fix hint (function name in
setup_steps) for fixable failures. `run_all_checks` orchestrates them in the
right dependency order.

All tests isolate filesystem state via `tmp_path` + env var overrides; no real
MCP server is ever contacted (we use a factory parameter for test injection).
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from typing import Iterator, Optional
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "secondbrain" / "scripts"))

import doctor_checks as doctor_module  # pyright: ignore[reportMissingImports]  # noqa: E402
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
    check_plugin_version_mismatch,
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
        for status in ("pass", "fail", "warning"):
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

    def test_pass_when_env_not_set_but_runtime_root_detectable(self, clean_env: None):
        del clean_env  # fixture consumed
        r = check_plugin_root()
        assert r.status == "pass"
        assert r.fixable is False

    def test_fail_when_env_not_set_and_runtime_root_unknown(
        self,
        clean_env: None,
        monkeypatch: pytest.MonkeyPatch,
    ):
        del clean_env  # fixture consumed
        monkeypatch.setattr(doctor_module, "_resolve_plugin_root", lambda candidate=None: None)
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
        r = check_obsidian_api_key(environment="code")
        assert r.status == "pass"
        assert r.fixable is False

    def test_pass_when_cowork_desktop_config_has_auth(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        for var in ("OBSIDIAN_API_KEY", "OBSIDIAN_MCP_PORT"):
            monkeypatch.delenv(var, raising=False)
        cfg = tmp_path / "claude_desktop_config.json"
        cfg.write_text(json.dumps({
            "mcpServers": {
                "obsidian": {
                    "command": "npx",
                    "args": [
                        "mcp-remote",
                        "http://localhost:27124/mcp",
                        "--header",
                        "Authorization:${AUTH}",
                    ],
                    "env": {"AUTH": "Bearer desktop-key"},
                }
            }
        }))
        r = check_obsidian_api_key(environment="cowork", desktop_config_path=cfg)
        assert r.status == "pass"
        assert "desktop config" in r.message.lower()

    def test_missing_api_key_not_fixable(self, clean_env: None):
        """Doctor cannot mint an API key — it must come from Obsidian's Connect
        MCP plugin. Advertising a fix here would be a lie because
        `setup_env_vars(api_key=None, port=None)` short-circuits to a no-op.
        """
        del clean_env
        r = check_obsidian_api_key(environment="code")
        assert r.status == "fail"
        assert r.fixable is False
        assert r.fix_function is None
        # Escalation should send users to init or shell config.
        assert ("/secondbrain:init" in r.message) or ("shell config" in r.message)

    def test_fail_when_empty(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OBSIDIAN_API_KEY", "")
        r = check_obsidian_api_key(environment="code")
        assert r.status == "fail"
        assert r.fixable is False

    def test_cowork_missing_config_degrades_to_warning(
        self,
        clean_env: None,
        tmp_path: Path,
    ):
        del clean_env
        r = check_obsidian_api_key(
            environment="cowork",
            desktop_config_path=tmp_path / "missing.json",
        )
        assert r.status == "warning"
        assert "session-level" in r.message.lower() or "desktop config" in r.message.lower()


# ---------------------------------------------------------------------------
# check_obsidian_mcp_port — Check 4
# ---------------------------------------------------------------------------

class TestCheckObsidianMcpPort:
    def test_pass_when_set(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OBSIDIAN_MCP_PORT", "27124")
        r = check_obsidian_mcp_port(environment="code")
        assert r.status == "pass"

    def test_pass_when_cowork_desktop_config_has_port(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        for var in ("OBSIDIAN_API_KEY", "OBSIDIAN_MCP_PORT"):
            monkeypatch.delenv(var, raising=False)
        cfg = tmp_path / "claude_desktop_config.json"
        cfg.write_text(json.dumps({
            "mcpServers": {
                "obsidian": {
                    "command": "npx",
                    "args": [
                        "mcp-remote",
                        "http://localhost:27124/mcp",
                        "--header",
                        "Authorization:${AUTH}",
                    ],
                    "env": {"AUTH": "Bearer desktop-key"},
                }
            }
        }))
        r = check_obsidian_mcp_port(environment="cowork", desktop_config_path=cfg)
        assert r.status == "pass"
        assert "27124" in r.message

    def test_missing_port_not_fixable(self, clean_env: None):
        """Doctor cannot guess which port to write — user must either run
        /secondbrain:init (which prompts for it) or export it themselves.
        """
        del clean_env
        r = check_obsidian_mcp_port(environment="code")
        assert r.status == "fail"
        assert r.fixable is False
        assert r.fix_function is None
        assert ("/secondbrain:init" in r.message) or ("OBSIDIAN_MCP_PORT" in r.message)

    def test_fail_when_non_numeric(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OBSIDIAN_MCP_PORT", "not-a-number")
        r = check_obsidian_mcp_port(environment="code")
        assert r.status == "fail"
        # Still not fixable — user must set a valid number themselves.
        assert r.fixable is False

    def test_cowork_missing_config_degrades_to_warning(
        self,
        clean_env: None,
        tmp_path: Path,
    ):
        del clean_env
        r = check_obsidian_mcp_port(
            environment="cowork",
            desktop_config_path=tmp_path / "missing.json",
        )
        assert r.status == "warning"
        assert "session-level" in r.message.lower() or "desktop config" in r.message.lower()


# ---------------------------------------------------------------------------
# check_obsidian_running — Check 5
# ---------------------------------------------------------------------------

class TestCheckObsidianRunning:
    def test_returns_check_result(self):
        # We don't know what the host has — just check shape.
        r = check_obsidian_running()
        assert r.status in ("pass", "fail")
        # Not fixable — user must open Obsidian manually
        assert r.fixable is False

    def test_passes_in_cowork_when_mcp_is_reachable(self, mock_mcp_client: MagicMock):
        r = check_obsidian_running(
            environment="cowork",
            client_factory=lambda: mock_mcp_client,
        )
        assert r.status == "pass"
        assert r.fixable is False

    def test_fails_in_cowork_when_mcp_is_unreachable(self):
        client = MagicMock()
        client.is_reachable.return_value = False
        r = check_obsidian_running(
            environment="cowork",
            client_factory=lambda: client,
        )
        assert r.status == "fail"
        assert r.fixable is False

    def test_cowork_factory_error_degrades_to_warning(self):
        r = check_obsidian_running(
            environment="cowork",
            client_factory=lambda: (_ for _ in ()).throw(RuntimeError("no config")),
        )
        assert r.status == "warning"
        assert "session-level" in r.message.lower() or "config" in r.message.lower()


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
        r = check_mcp_connection(environment="code", client_factory=factory)
        assert r.status == "fail"
        assert "port not set" in r.message or "factory" in r.message.lower() or "unreachable" in r.message.lower()

    def test_cowork_factory_error_degrades_to_warning(self):
        def factory():
            raise RuntimeError("desktop config unavailable")
        r = check_mcp_connection(environment="cowork", client_factory=factory)
        assert r.status == "warning"
        assert "session-level" in r.message.lower() or "desktop config" in r.message.lower()


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


# ---------------------------------------------------------------------------
# check_plugin_version_mismatch — Check 20
# ---------------------------------------------------------------------------

class TestCheckPluginVersionMismatch:
    def _write_installed_plugin(
        self,
        plugin_root: Path,
        plugin_version: str,
        marketplace_version: Optional[str] = None,
    ) -> None:
        if marketplace_version is None:
            marketplace_version = plugin_version
        (plugin_root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
        (plugin_root / ".claude-plugin" / "plugin.json").write_text(
            json.dumps(
                {
                    "name": "secondbrain",
                    "version": plugin_version,
                    "description": "x",
                    "repository": "https://github.com/stjepanvrbic/secondbrain",
                }
            )
        )
        repo_root = plugin_root.parent
        (repo_root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
        (repo_root / ".claude-plugin" / "marketplace.json").write_text(
            json.dumps(
                {
                    "metadata": {"version": marketplace_version},
                    "plugins": [{"name": "secondbrain", "version": marketplace_version, "source": "./secondbrain"}],
                }
            )
        )

    def test_warning_when_latest_release_is_newer(self, tmp_path: Path):
        plugin_root = tmp_path / "secondbrain"
        plugin_root.mkdir()
        self._write_installed_plugin(plugin_root, "3.5.11")
        r = check_plugin_version_mismatch(
            plugin_root,
            latest_release_fetcher=lambda _repo: "v3.5.12",
        )
        assert r.status == "warning"
        assert "3.5.12" in r.message
        assert "reinstall" in r.message.lower()

    def test_pass_when_installed_matches_latest_release(self, tmp_path: Path):
        plugin_root = tmp_path / "secondbrain"
        plugin_root.mkdir()
        self._write_installed_plugin(plugin_root, "3.5.12")
        r = check_plugin_version_mismatch(
            plugin_root,
            latest_release_fetcher=lambda _repo: "v3.5.12",
        )
        assert r.status == "pass"

    def test_uses_installed_runtime_plugin_version(self, tmp_path: Path):
        plugin_root = tmp_path / "secondbrain"
        plugin_root.mkdir()
        self._write_installed_plugin(
            plugin_root,
            plugin_version="3.5.11",
            marketplace_version="3.5.12",
        )
        r = check_plugin_version_mismatch(
            plugin_root,
            latest_release_fetcher=lambda _repo: "v3.5.11",
        )
        assert r.status == "pass"
        assert "runtime plugin version 3.5.11" in r.message

    def test_warning_when_latest_release_cannot_be_fetched(self, tmp_path: Path):
        plugin_root = tmp_path / "secondbrain"
        plugin_root.mkdir()
        self._write_installed_plugin(plugin_root, "3.5.12")

        def _boom(_repo: str) -> str:
            raise OSError("offline")

        r = check_plugin_version_mismatch(
            plugin_root,
            latest_release_fetcher=_boom,
        )
        assert r.status == "warning"

    def test_supports_repo_root_with_nested_runtime_plugin(self, tmp_path: Path):
        plugin_root = tmp_path / "secondbrain"
        plugin_root.mkdir()
        self._write_installed_plugin(plugin_root, "3.5.13")
        r = check_plugin_version_mismatch(
            tmp_path,
            latest_release_fetcher=lambda _repo: "v3.5.13",
        )
        assert r.status == "pass"

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
        # We can't CronList from a test, so this should degrade to a warning.
        r = check_scheduled_tasks("code")
        assert r.status == "warning"

    def test_cowork_mode(self):
        r = check_scheduled_tasks("cowork")
        assert r.status == "warning"
        assert ".scheduled-tasks" not in r.message
        assert "scheduled-tasks tool" in r.message.lower() or "session layer" in r.message.lower()


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

    def test_warning_when_log_missing(self, broken_vault: Path):
        r = check_last_dream_protocol_run(broken_vault)
        assert r.status == "warning"
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

    def test_fail_when_mcp_client_none(self, healthy_vault: Path):
        r = check_vault_identity_cross(healthy_vault, mcp_client=None)
        assert r.status == "warning"

    def test_fail_when_mcp_cannot_read_dotfile(
        self,
        healthy_vault: Path,
        mock_mcp_client: MagicMock,
    ):
        mock_mcp_client.vault_read.side_effect = FileNotFoundError("File not found")
        r = check_vault_identity_cross(healthy_vault, mcp_client=mock_mcp_client)
        assert r.status == "warning"
        assert (
            "dotfile" in r.message.lower()
            or "cannot prove" in r.message.lower()
            or "vault_path" in r.message.lower()
        )


class TestCheckVaultsConfig:
    def test_honors_vaults_config_override(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ):
        config_path = tmp_path / "config" / "secondbrain" / "vaults.json"
        config_path.parent.mkdir(parents=True)
        config_path.write_text(json.dumps({
            "schema_version": 1,
            "vaults": [],
            "active_vault_id": "vault-1",
        }))
        monkeypatch.setenv("SECONDBRAIN_VAULTS_CONFIG", str(config_path))
        r = doctor_module.check_vaults_config()
        assert r.status == "pass"


# ---------------------------------------------------------------------------
# check_hot_memory_schema — Check 14 (T11: reads brain/hot-memory.md)
# ---------------------------------------------------------------------------

class TestCheckHotMemorySchema:
    """T11 repoints this check at <vault>/brain/hot-memory.md and has it
    shell out to the real `validate_hot_memory.py --quiet` script."""

    def _real_plugin_root(self) -> Path:
        """Return the live repo plugin root so `validate_hot_memory.py`
        exists and can actually be invoked."""
        return Path(__file__).resolve().parent.parent

    def test_fail_when_validator_missing(self, tmp_path: Path):
        """Partial install is a real failure, not a skip."""
        plugin_root = tmp_path / "plugin"
        plugin_root.mkdir()
        # No validate_hot_memory.py present at all.
        r = check_hot_memory_schema(tmp_path, plugin_root)
        assert r.status == "fail"
        assert (
            "validate_hot_memory" in r.message
            or "not present" in r.message.lower()
            or "partial" in r.message.lower()
        )

    def test_fail_when_validator_present_but_file_missing(self, tmp_path: Path):
        """Hot-memory file missing in the new location → fail with a pointer."""
        plugin_root = self._real_plugin_root()
        vault = tmp_path / "vault"
        (vault / "brain").mkdir(parents=True)
        # No brain/hot-memory.md — the file is what's missing.
        r = check_hot_memory_schema(vault, plugin_root)
        assert r.status == "fail"
        assert "hot-memory" in r.message.lower()
        assert "brain/hot-memory.md" in r.message or "brain" in r.message

    def test_runtime_bundle_layout_supports_validator_lookup(self, tmp_path: Path):
        plugin_root = tmp_path / "plugin"
        (plugin_root / "scripts").mkdir(parents=True)
        (plugin_root / "scripts" / "validate_hot_memory.py").write_text(
            "import sys\n"
            "sys.exit(0)\n"
        )
        vault = tmp_path / "vault"
        (vault / "brain").mkdir(parents=True)
        r = check_hot_memory_schema(vault, plugin_root)
        assert r.status == "fail"
        assert r.fix_function == "create_hot_memory_initial"

    def test_file_missing_is_fixable_by_create_hot_memory_initial(self, tmp_path: Path):
        """T14 makes the "file missing" branch auto-fixable via
        setup_steps.create_hot_memory_initial so doctor --treat can
        seed the INITIAL_TEMPLATE without falling back to
        /secondbrain:dream-protocol.
        """
        plugin_root = self._real_plugin_root()
        vault = tmp_path / "vault"
        (vault / "brain").mkdir(parents=True)
        r = check_hot_memory_schema(vault, plugin_root)
        assert r.status == "fail"
        assert r.fixable is True, (
            "missing hot-memory.md must be marked fixable — T14 wires "
            "create_hot_memory_initial as the treatment path."
        )
        assert r.fix_function == "create_hot_memory_initial", (
            "fix_function must name create_hot_memory_initial so the "
            "treatment dispatcher in doctor_checks.run_fixable_treatments "
            "can look it up on setup_steps."
        )

    def test_pass_when_file_valid(self, tmp_path: Path):
        """A valid hot-memory.md (INITIAL_TEMPLATE) should pass."""
        import sys
        sys.path.insert(
            0,
            str(Path(__file__).resolve().parent.parent / "secondbrain" / "scripts"),
        )
        from hot_memory_schema import INITIAL_TEMPLATE  # type: ignore[reportMissingImports]

        plugin_root = self._real_plugin_root()
        vault = tmp_path / "vault"
        (vault / "brain").mkdir(parents=True)
        (vault / "brain" / "hot-memory.md").write_text(INITIAL_TEMPLATE)
        r = check_hot_memory_schema(vault, plugin_root)
        assert r.status == "pass"

    def test_fail_when_file_invalid(self, tmp_path: Path):
        """A malformed hot-memory.md fails — no frontmatter means the schema
        rejects it and doctor reports a specific error."""
        plugin_root = self._real_plugin_root()
        vault = tmp_path / "vault"
        (vault / "brain").mkdir(parents=True)
        (vault / "brain" / "hot-memory.md").write_text(
            "# this has no frontmatter and no required sections\n"
        )
        r = check_hot_memory_schema(vault, plugin_root)
        assert r.status == "fail"
        # Message should hint at what's wrong.
        assert "validation" in r.message.lower() or "hot-memory" in r.message.lower()


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

    def test_fail_when_path_not_a_git_repo(self, tmp_path: Path):
        r = check_core_hooks_path(tmp_path)
        assert r.status == "fail"


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

    def test_upstream_mcp_failures_do_not_hide_as_skip(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        # No MCP env → MCP connection must fail explicitly, not hide as skip.
        for var in ("OBSIDIAN_API_KEY", "OBSIDIAN_MCP_PORT"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path))

        vault = tmp_path / "vault"
        vault.mkdir()
        results = run_all_checks(
            vault_path=vault,
            plugin_root=tmp_path,
            environment="code",
        )
        by_name = {r.name: r for r in results}
        assert by_name["mcp_connection"].status == "fail"
        assert all(r.status != "skip" for r in results)

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
            environment="code",
        )
        failures = [r for r in results if r.status == "fail"]
        assert len(failures) > 0

    def test_vault_unreachable_cascades_failures_not_skips(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """When vault_reachable fails, downstream filesystem checks — including
        scheduled_tasks — must fail explicitly so the report does not hide the
        broken vault behind skip statuses while keeping the result shape
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
            environment="code",
        )
        by_name = {r.name: r for r in results}
        assert "vault_reachable" in by_name
        assert by_name["vault_reachable"].status == "fail"
        # Every downstream vault-side check must be present as a fail —
        # scheduled_tasks included.
        for name in (
            "manifest", "log_md", "profile", "standard_folders",
            "scheduled_tasks", "last_dream_protocol_run",
            "hot_memory_schema", "ingest_log_recent_failures",
        ):
            assert name in by_name, f"{name} missing from cascade failure set"
            assert by_name[name].status == "fail", (
                f"{name} should be fail when vault unreachable, "
                f"got {by_name[name].status}"
            )

    def test_cowork_desktop_config_allows_mcp_checks_without_env(
        self,
        healthy_vault: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        mock_mcp_client: MagicMock,
    ):
        for var in ("OBSIDIAN_API_KEY", "OBSIDIAN_MCP_PORT"):
            monkeypatch.delenv(var, raising=False)
        cfg = tmp_path / "claude_desktop_config.json"
        cfg.write_text(json.dumps({
            "mcpServers": {
                "obsidian": {
                    "command": "npx",
                    "args": [
                        "mcp-remote",
                        "http://localhost:27124/mcp",
                        "--header",
                        "Authorization:${AUTH}",
                    ],
                    "env": {"AUTH": "Bearer desktop-key"},
                }
            }
        }))
        vault_id = json.loads((healthy_vault / ".secondbrain-installed").read_text())["vault_id"]
        mock_mcp_client.vault_read.return_value = json.dumps({"vault_id": vault_id})
        results = run_all_checks(
            vault_path=healthy_vault,
            plugin_root=Path(__file__).resolve().parent.parent / "secondbrain",
            desktop_config_path=cfg,
            environment="cowork",
            mcp_client_factory=lambda: mock_mcp_client,
        )
        by_name = {r.name: r for r in results}
        assert by_name["obsidian_api_key"].status == "pass"
        assert by_name["obsidian_mcp_port"].status == "pass"
        assert by_name["obsidian_running"].status == "pass"
        assert by_name["mcp_connection"].status == "pass"


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
