"""Tests for init_obsidian.py — platform detection, installation, scaffolding."""

import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "secondbrain" / "scripts"))

from init_obsidian import (  # type: ignore[reportMissingImports]
    detect_platform, is_wsl, find_obsidian, find_existing_vaults,
    install_plugin, enable_plugins, configure_mcp_plugin, detect_shell,
    set_env_vars, scaffold_vault, import_notes, main,
    ensure_obsidian_running,
    REQUIRED_DIRS, CRITICAL_FILES, PLUGINS,
)


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------

class TestDetectPlatform:
    def test_returns_string(self):
        result = detect_platform()
        assert result in ("macos", "linux", "windows")

    @patch("platform.system", return_value="Darwin")
    def test_macos(self, _):
        assert detect_platform() == "macos"

    @patch("platform.system", return_value="Linux")
    @patch("platform.release", return_value="5.15.0-generic")
    def test_linux(self, *_):
        assert detect_platform() == "linux"

    @patch("platform.system", return_value="Windows")
    def test_windows(self, _):
        assert detect_platform() == "windows"


class TestIsWsl:
    def test_returns_bool(self):
        assert isinstance(is_wsl(), bool)

    @patch("pathlib.Path.read_text", return_value="Linux version 5.15.0-microsoft-standard-WSL2")
    def test_detects_wsl(self, _):
        assert is_wsl() is True

    @patch("pathlib.Path.read_text", return_value="Linux version 5.15.0-generic")
    def test_not_wsl(self, _):
        assert is_wsl() is False

    @patch("pathlib.Path.read_text", side_effect=OSError("no proc"))
    def test_no_proc(self, _):
        assert is_wsl() is False


# ---------------------------------------------------------------------------
# Obsidian detection
# ---------------------------------------------------------------------------

class TestFindObsidian:
    def test_finds_on_current_platform(self):
        # Just verify it returns Path or None, doesn't crash
        result = find_obsidian(detect_platform())
        assert result is None or isinstance(result, Path)

    @patch("pathlib.Path.exists", return_value=True)
    def test_finds_by_known_path(self, _):
        result = find_obsidian("macos")
        assert result is not None


class TestFindExistingVaults:
    def test_with_config(self, tmp_path: Path):
        config_dir = tmp_path / "obsidian"
        config_dir.mkdir()
        vault_dir = tmp_path / "my-vault"
        vault_dir.mkdir()

        config = {"vaults": {"abc123": {"path": str(vault_dir)}}}
        (config_dir / "obsidian.json").write_text(json.dumps(config))

        with patch.dict("init_obsidian.OBSIDIAN_CONFIG_PATHS", {"macos": config_dir}):
            vaults = find_existing_vaults("macos")
            assert len(vaults) == 1
            assert vaults[0][1] == vault_dir

    def test_no_config(self, tmp_path: Path):
        with patch.dict("init_obsidian.OBSIDIAN_CONFIG_PATHS", {"macos": tmp_path / "nonexistent"}):
            assert find_existing_vaults("macos") == []

    def test_invalid_json(self, tmp_path: Path):
        config_dir = tmp_path / "obsidian"
        config_dir.mkdir()
        (config_dir / "obsidian.json").write_text("not json")
        with patch.dict("init_obsidian.OBSIDIAN_CONFIG_PATHS", {"macos": config_dir}):
            assert find_existing_vaults("macos") == []


# ---------------------------------------------------------------------------
# ensure_obsidian_running — launch + timeout
# ---------------------------------------------------------------------------


class TestEnsureObsidianRunningTimeout:
    """The foreground launch wait is capped at 60 seconds (Theme 5.1).

    We don't test the happy path here — that's exercised indirectly by the
    full-run tests. What we test is: if Obsidian never shows up, the function
    returns False instead of hanging forever, and the user sees the specific
    error message the spec mandates.
    """

    def test_dry_run_short_circuits(self):
        # dry_run path should never shell out to pgrep or tasklist
        with patch("subprocess.run") as run_mock, patch("subprocess.Popen") as popen_mock:
            run_mock.return_value = MagicMock(returncode=1)
            result = ensure_obsidian_running("macos", dry_run=True)
            assert result is True
            popen_mock.assert_not_called()

    def test_timeout_returns_false_with_message(self, capsys):
        # Simulate Obsidian never appearing. pgrep always returns rc=1,
        # time.monotonic ticks forward in 10-second jumps so the loop bails
        # after ~6 iterations instead of taking an actual minute.
        fake_clock = {"now": 1000.0}

        def fake_monotonic():
            fake_clock["now"] += 10.0
            return fake_clock["now"]

        def fake_run(cmd, *args, **kwargs):
            del args, kwargs  # subprocess.run passes extra args/flags; we only inspect cmd
            mock = MagicMock()
            mock.returncode = 0 if cmd[:2] == ["open", "-a"] else 1
            mock.stdout = ""
            return mock

        with patch("time.monotonic", side_effect=fake_monotonic), \
             patch("time.sleep"), \
             patch("subprocess.run", side_effect=fake_run):
            result = ensure_obsidian_running("macos")

        assert result is False
        out = capsys.readouterr().out
        assert "did not launch within 60 seconds" in out
        # Make sure all three diagnostic hints appear
        assert "headless Linux" in out
        assert "crashed during startup" in out
        assert "slow machine" in out


# ---------------------------------------------------------------------------
# Plugin installation
# ---------------------------------------------------------------------------

class TestInstallPlugin:
    def test_already_installed(self, tmp_vault: Path):
        plugin_dir = tmp_vault / ".obsidian" / "plugins" / "dataview"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "main.js").write_text("// plugin")
        (plugin_dir / "manifest.json").write_text("{}")

        result = install_plugin(tmp_vault, "dataview", PLUGINS["dataview"])
        assert result is True

    def test_dry_run(self, tmp_vault: Path):
        result = install_plugin(tmp_vault, "dataview", PLUGINS["dataview"], dry_run=True)
        assert result is True
        assert not (tmp_vault / ".obsidian" / "plugins" / "dataview" / "main.js").exists()

    @patch("init_obsidian.fetch_latest_release", return_value=None)
    def test_fetch_fails(self, _, tmp_vault: Path):
        result = install_plugin(tmp_vault, "dataview", PLUGINS["dataview"])
        assert result is False


class TestEnablePlugins:
    def test_creates_file(self, tmp_vault: Path):
        (tmp_vault / ".obsidian").mkdir(exist_ok=True)
        enable_plugins(tmp_vault, ["dataview", "connect-mcp"])
        plugins_file = tmp_vault / ".obsidian" / "community-plugins.json"
        assert plugins_file.exists()
        data = json.loads(plugins_file.read_text())
        assert "dataview" in data
        assert "connect-mcp" in data

    def test_appends_to_existing(self, tmp_vault: Path):
        (tmp_vault / ".obsidian").mkdir(exist_ok=True)
        plugins_file = tmp_vault / ".obsidian" / "community-plugins.json"
        plugins_file.write_text('["existing-plugin"]')
        enable_plugins(tmp_vault, ["dataview"])
        data = json.loads(plugins_file.read_text())
        assert "existing-plugin" in data
        assert "dataview" in data

    def test_no_duplicates(self, tmp_vault: Path):
        (tmp_vault / ".obsidian").mkdir(exist_ok=True)
        plugins_file = tmp_vault / ".obsidian" / "community-plugins.json"
        plugins_file.write_text('["dataview"]')
        enable_plugins(tmp_vault, ["dataview"])
        data = json.loads(plugins_file.read_text())
        assert data.count("dataview") == 1

    def test_dry_run(self, tmp_vault: Path):
        (tmp_vault / ".obsidian").mkdir(exist_ok=True)
        enable_plugins(tmp_vault, ["dataview"], dry_run=True)
        assert not (tmp_vault / ".obsidian" / "community-plugins.json").exists()


# ---------------------------------------------------------------------------
# REST API config
# ---------------------------------------------------------------------------

class TestConfigureMcpPlugin:
    def test_reads_existing_config(self, tmp_vault: Path):
        config_dir = tmp_vault / ".obsidian" / "plugins" / "connect-mcp"
        config_dir.mkdir(parents=True)
        (config_dir / "data.json").write_text(json.dumps({"port": 27124, "apiKey": "test-key-123"}))

        port, key = configure_mcp_plugin(tmp_vault)
        assert port == 27124
        assert key == "test-key-123"

    def test_writes_default_config(self, tmp_vault: Path):
        port, key = configure_mcp_plugin(tmp_vault)
        assert port == 27124
        assert key is None
        config = tmp_vault / ".obsidian" / "plugins" / "connect-mcp" / "data.json"
        assert config.exists()

    def test_dry_run(self, tmp_vault: Path):
        port, _ = configure_mcp_plugin(tmp_vault, dry_run=True)
        assert port == 27124
        config = tmp_vault / ".obsidian" / "plugins" / "connect-mcp" / "data.json"
        assert not config.exists()


# ---------------------------------------------------------------------------
# Shell detection + env vars
# ---------------------------------------------------------------------------

class TestDetectShell:
    def test_returns_string(self):
        assert detect_shell() in ("zsh", "bash", "fish", "powershell")

    @patch.dict(os.environ, {"SHELL": "/bin/zsh"})
    def test_zsh(self):
        assert detect_shell() == "zsh"

    @patch.dict(os.environ, {"SHELL": "/bin/bash"})
    def test_bash(self):
        assert detect_shell() == "bash"

    @patch.dict(os.environ, {"SHELL": "/usr/bin/fish"})
    def test_fish(self):
        assert detect_shell() == "fish"


class TestSetEnvVars:
    def test_appends_to_zshrc(self, tmp_path: Path):
        zshrc = tmp_path / ".zshrc"
        zshrc.write_text("# existing content\n")
        with patch.dict("init_obsidian.SHELL_CONFIGS", {"zsh": zshrc}):
            set_env_vars(27124, "test-key", "zsh")
        content = zshrc.read_text()
        assert "OBSIDIAN_MCP_PORT" in content
        assert "OBSIDIAN_API_KEY" in content
        assert "test-key" in content

    def test_skips_existing(self, tmp_path: Path):
        zshrc = tmp_path / ".zshrc"
        zshrc.write_text('export OBSIDIAN_MCP_PORT="27124"\nexport OBSIDIAN_API_KEY="old-key"\n')
        with patch.dict("init_obsidian.SHELL_CONFIGS", {"zsh": zshrc}):
            set_env_vars(27124, "new-key", "zsh")
        content = zshrc.read_text()
        assert "new-key" not in content  # shouldn't duplicate

    def test_fish_syntax(self, tmp_path: Path):
        config = tmp_path / "config.fish"
        config.write_text("# fish config\n")
        with patch.dict("init_obsidian.SHELL_CONFIGS", {"fish": config}):
            set_env_vars(27124, "test-key", "fish")
        content = config.read_text()
        assert "set -gx OBSIDIAN_MCP_PORT" in content

    def test_dry_run(self, tmp_path: Path):
        zshrc = tmp_path / ".zshrc"
        zshrc.write_text("# existing\n")
        with patch.dict("init_obsidian.SHELL_CONFIGS", {"zsh": zshrc}):
            set_env_vars(27124, "key", "zsh", dry_run=True)
        assert "OBSIDIAN_MCP_PORT" not in zshrc.read_text()

    def test_no_api_key(self, tmp_path: Path):
        zshrc = tmp_path / ".zshrc"
        zshrc.write_text("")
        with patch.dict("init_obsidian.SHELL_CONFIGS", {"zsh": zshrc}):
            set_env_vars(27124, None, "zsh")
        content = zshrc.read_text()
        assert "OBSIDIAN_MCP_PORT" in content
        assert "OBSIDIAN_API_KEY" not in content


# ---------------------------------------------------------------------------
# Vault scaffolding
# ---------------------------------------------------------------------------

class TestScaffoldVault:
    def test_creates_all_dirs_and_files(self, tmp_path: Path):
        count = scaffold_vault(tmp_path)
        assert count > 0
        for d in REQUIRED_DIRS:
            assert (tmp_path / d).is_dir(), f"Missing dir: {d}"
        for f in CRITICAL_FILES:
            assert (tmp_path / f).exists(), f"Missing file: {f}"

    def test_idempotent(self, tmp_path: Path):
        scaffold_vault(tmp_path)
        count = scaffold_vault(tmp_path)
        assert count == 0  # nothing new to create

    def test_preserves_existing(self, tmp_path: Path):
        (tmp_path / "brain").mkdir(parents=True)
        (tmp_path / "brain" / "status.md").write_text("# My custom status\n")
        scaffold_vault(tmp_path)
        assert "My custom status" in (tmp_path / "brain" / "status.md").read_text()

    def test_dry_run(self, tmp_path: Path):
        count = scaffold_vault(tmp_path, dry_run=True)
        assert count > 0
        # Nothing actually created
        assert not (tmp_path / "brain" / "status.md").exists()

    def test_creates_obsidian_dir(self, tmp_path: Path):
        scaffold_vault(tmp_path)
        assert (tmp_path / ".obsidian").is_dir()

    def test_status_has_no_commitments(self, tmp_path: Path):
        scaffold_vault(tmp_path)
        content = (tmp_path / "brain" / "status.md").read_text()
        assert "commitment" not in content.lower()


# ---------------------------------------------------------------------------
# Note import
# ---------------------------------------------------------------------------

class TestImportNotes:
    def test_copies_md_files(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        source = tmp_path / "notes"
        source.mkdir()
        (source / "note1.md").write_text("# Note 1")
        (source / "note2.md").write_text("# Note 2")

        count = import_notes(vault, source)
        assert count == 2
        assert (vault / "inbox" / "note1.md").exists()
        assert (vault / "inbox" / "note2.md").exists()

    def test_preserves_originals(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        source = tmp_path / "notes"
        source.mkdir()
        (source / "important.md").write_text("# Important")

        import_notes(vault, source)
        assert (source / "important.md").exists()
        assert (source / "important.md").read_text() == "# Important"

    def test_flattens_subdirectories(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        source = tmp_path / "notes"
        (source / "work").mkdir(parents=True)
        (source / "work" / "meeting.md").write_text("# Meeting")

        import_notes(vault, source)
        assert (vault / "inbox" / "work--meeting.md").exists()

    def test_skips_unsupported_types(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        source = tmp_path / "notes"
        source.mkdir()
        (source / "note.md").write_text("# Note")
        (source / "image.png").write_bytes(b"\x89PNG")
        (source / "data.csv").write_text("a,b,c")

        count = import_notes(vault, source)
        assert count == 2  # .md + .csv (csv is importable)
        assert (vault / "inbox" / "note.md").exists()

    def test_dry_run_no_changes(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        source = tmp_path / "notes"
        source.mkdir()
        (source / "note.md").write_text("# Note")

        count = import_notes(vault, source, dry_run=True)
        assert count == 1
        assert not (vault / "inbox").exists()

    def test_handles_name_collision(self, tmp_path: Path):
        vault = tmp_path / "vault"
        (vault / "inbox").mkdir(parents=True)
        (vault / "inbox" / "note.md").write_text("# Existing")
        source = tmp_path / "notes"
        source.mkdir()
        (source / "note.md").write_text("# New")

        import_notes(vault, source)
        assert (vault / "inbox" / "note-1.md").exists()
        assert (vault / "inbox" / "note-1.md").read_text() == "# New"
        assert (vault / "inbox" / "note.md").read_text() == "# Existing"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class TestMain:
    def test_dry_run_full(self, tmp_path: Path):
        code = main(["--vault-path", str(tmp_path), "--skip-install", "--dry-run"])
        assert code == 0
        # Nothing should have been created in dry run
        assert not (tmp_path / "brain" / "status.md").exists()

    def test_full_setup_skip_install(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        import uuid as _uuid
        vault = tmp_path / "test-vault"
        fake_rc = tmp_path / ".zshrc"
        fake_rc.write_text("")
        monkeypatch.setenv(
            "SECONDBRAIN_VAULTS_CONFIG",
            str(tmp_path / "config" / "vaults.json"),
        )
        with patch.dict("init_obsidian.SHELL_CONFIGS", {"zsh": fake_rc}):
            code = main(["--vault-path", str(vault), "--skip-install"])
        assert code == 0
        assert vault.is_dir()
        assert (vault / "brain" / "status.md").exists()
        assert (vault / "entities").is_dir()
        assert (vault / ".obsidian").is_dir()

        marker = vault / ".secondbrain-installed"
        assert marker.exists()
        data = json.loads(marker.read_text())
        # T3: the marker now carries a stable vault_id (UUID4) and timestamps.
        assert "vault_id" in data
        assert _uuid.UUID(data["vault_id"]).version == 4
        assert "installed_at" in data
        assert "last_init_at" in data

    def test_idempotent_run(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        vault = tmp_path / "vault"
        fake_rc = tmp_path / ".zshrc"
        fake_rc.write_text("")
        monkeypatch.setenv(
            "SECONDBRAIN_VAULTS_CONFIG",
            str(tmp_path / "config" / "vaults.json"),
        )
        with patch.dict("init_obsidian.SHELL_CONFIGS", {"zsh": fake_rc}):
            main(["--vault-path", str(vault), "--skip-install"])
            # Run again — should still succeed
            code = main(["--vault-path", str(vault), "--skip-install"])
        assert code == 0

    def test_auto_detect_vault(self, tmp_path: Path):
        with patch("init_obsidian.find_existing_vaults", return_value=[]), \
             patch("init_obsidian.find_obsidian", return_value=Path("/fake/obsidian")), \
             patch("init_obsidian.default_vault_path", return_value=tmp_path / "auto-vault"):
            code = main(["--skip-install", "--dry-run"])
            assert code == 0


# ---------------------------------------------------------------------------
# T6: delegation to setup_steps — env var writing
# ---------------------------------------------------------------------------

class TestMainDelegatesEnvVarsToSetupSteps:
    """Step 7 of main() must delegate env var writing to
    ``setup_steps.setup_env_vars`` instead of calling the inline
    ``set_env_vars`` helper directly. The inline helper stays around
    because setup_steps wraps it, but main() routes through setup_steps
    so doctor and init share exactly one code path for this write.
    """

    def test_main_calls_setup_steps_setup_env_vars(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Step 7 in main() must call setup_steps.setup_env_vars when the
        MCP plugin hands back a port. We patch setup_steps.setup_env_vars
        and verify it receives (api_key, port, ...) — NOT (port, api_key,
        shell, dry_run) which is the inline signature.
        """
        import setup_steps  # type: ignore[reportMissingImports]

        vault = tmp_path / "test-vault"
        fake_rc = tmp_path / ".zshrc"
        fake_rc.write_text("")
        monkeypatch.setenv(
            "SECONDBRAIN_VAULTS_CONFIG",
            str(tmp_path / "config" / "vaults.json"),
        )

        # Force configure_mcp_plugin to return a fixed (port, api_key) so
        # the delegation branch fires with concrete values we can assert on.
        with patch(
            "init_obsidian.configure_mcp_plugin",
            return_value=(27124, "fake-api-key"),
        ), patch.object(
            setup_steps,
            "setup_env_vars",
            wraps=setup_steps.setup_env_vars,
        ) as spy, patch.dict(
            "init_obsidian.SHELL_CONFIGS", {"zsh": fake_rc}
        ):
            code = main(["--vault-path", str(vault), "--skip-install"])

        assert code == 0
        # The delegation must have fired at least once.
        assert spy.call_count >= 1, (
            "init_obsidian.main() must delegate env var writing to "
            "setup_steps.setup_env_vars instead of calling the inline helper."
        )
        # Verify the call signature matches setup_steps contract:
        # setup_env_vars(api_key, port, ...).
        call_args = spy.call_args_list[0]
        # Accept either positional or keyword form.
        passed_port = call_args.kwargs.get("port")
        passed_key = call_args.kwargs.get("api_key")
        if passed_port is None and len(call_args.args) >= 2:
            passed_key = call_args.args[0]
            passed_port = call_args.args[1]
        assert passed_port == 27124
        assert passed_key == "fake-api-key"

    def test_setup_env_vars_writes_file_via_delegation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """End-to-end: when main() delegates to setup_steps.setup_env_vars,
        the shell config file gets the env var lines written. This proves
        the delegation doesn't break the observable behavior.
        """
        vault = tmp_path / "e2e-vault"
        fake_rc = tmp_path / ".zshrc"
        fake_rc.write_text("# pre-existing\n")
        monkeypatch.setenv(
            "SECONDBRAIN_VAULTS_CONFIG",
            str(tmp_path / "config" / "vaults.json"),
        )
        monkeypatch.setenv("SHELL", "/bin/zsh")

        with patch(
            "init_obsidian.configure_mcp_plugin",
            return_value=(27124, "e2e-api-key"),
        ), patch.dict("init_obsidian.SHELL_CONFIGS", {"zsh": fake_rc}):
            code = main(["--vault-path", str(vault), "--skip-install"])

        assert code == 0
        content = fake_rc.read_text()
        assert 'OBSIDIAN_MCP_PORT="27124"' in content
        assert 'OBSIDIAN_API_KEY="e2e-api-key"' in content
