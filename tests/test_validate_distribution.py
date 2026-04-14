"""Tests for marketplace/distribution validation helpers."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "secondbrain" / "scripts"))

from validate_distribution import (  # type: ignore[reportMissingImports]
    inspect_cowork_runtime_state,
    main,
    validate_cowork_runtime_state,
    validate_marketplace_layout,
)


def _write_plugin_bundle(root: Path, *, version: str) -> Path:
    manifest_dir = root / ".claude-plugin"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "plugin.json").write_text(
        json.dumps({"name": "secondbrain", "version": version})
    )
    return root


def _write_repo(root: Path, *, version: str, metadata_version: str | None = None) -> Path:
    plugin_root = root / "secondbrain"
    _write_plugin_bundle(plugin_root, version=version)
    (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (root / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps(
            {
                "name": "secondbrain",
                "owner": {"name": "test"},
                "metadata": {
                    "description": "test",
                    "version": metadata_version if metadata_version is not None else version,
                },
                "plugins": [
                    {"name": "secondbrain", "version": version, "source": "./secondbrain"}
                ],
            }
        )
    )
    return root


def _write_rpm_manifest(path: Path, *, plugin_id: str = "plugin_123") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "plugins": [
                    {
                        "id": plugin_id,
                        "name": "secondbrain",
                        "marketplaceName": "secondbrain",
                    }
                ]
            }
        )
    )
    return path


def _write_known_marketplaces(
    path: Path,
    *,
    install_location: Path | None = None,
    include_secondbrain: bool = True,
    source_type: str = "github",
    repo: str = "stjepanvrbic/secondbrain",
) -> Path:
    data: dict[str, object] = {}
    if include_secondbrain:
        source: dict[str, str] = {"source": source_type}
        if source_type == "github":
            source["repo"] = repo
        elif source_type == "directory":
            source["path"] = str(install_location or path.parent / "secondbrain")
        data["secondbrain"] = {
            "source": source,
            "installLocation": str(install_location or path.parent / "secondbrain"),
            "lastUpdated": "2026-04-14T23:11:19.851Z",
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


def _write_settings(path: Path, *, enable_marketplace: bool, enable_uploads: bool = False) -> Path:
    enabled_plugins: dict[str, bool] = {}
    if enable_marketplace:
        enabled_plugins["secondbrain@secondbrain"] = True
    if enable_uploads:
        enabled_plugins["secondbrain@My Uploads"] = True
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"enabledPlugins": enabled_plugins}))
    return path


class TestValidateMarketplaceLayout:
    def test_accepts_valid_repo_layout(self, tmp_path: Path):
        repo_root = _write_repo(tmp_path, version="3.5.25")

        assert validate_marketplace_layout(repo_root) == []

    def test_rejects_mismatched_marketplace_metadata_version(self, tmp_path: Path):
        repo_root = _write_repo(tmp_path, version="3.5.25", metadata_version="3.5.24")

        errors = validate_marketplace_layout(repo_root)

        assert any("metadata.version" in error for error in errors)

    def test_rejects_legacy_release_manifest(self, tmp_path: Path):
        repo_root = _write_repo(tmp_path, version="3.5.25")
        (repo_root / "secondbrain" / ".claude-plugin" / "release.json").write_text("{}")

        errors = validate_marketplace_layout(repo_root)

        assert any("release.json" in error for error in errors)

    def test_rejects_missing_plugin_manifest(self, tmp_path: Path):
        repo_root = tmp_path
        (repo_root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
        (repo_root / ".claude-plugin" / "marketplace.json").write_text(
            json.dumps(
                {
                    "name": "secondbrain",
                    "owner": {"name": "test"},
                    "metadata": {"description": "test", "version": "3.5.25"},
                    "plugins": [
                        {"name": "secondbrain", "version": "3.5.25", "source": "./secondbrain"}
                    ],
                }
            )
        )
        (repo_root / "secondbrain").mkdir()

        errors = validate_marketplace_layout(repo_root)

        assert any("plugin.json" in error for error in errors)


class TestCoworkRuntimeState:
    def test_reports_marketplace_checkout_installed_runtime_and_session_versions_separately(self, tmp_path: Path):
        marketplace_json = _write_repo(tmp_path / "repo", version="3.5.23") / ".claude-plugin" / "marketplace.json"
        checkout_root = tmp_path / "cowork_plugins" / "marketplaces" / "secondbrain"
        (checkout_root / ".git").mkdir(parents=True)
        known_marketplaces = _write_known_marketplaces(
            tmp_path / "cowork_plugins" / "known_marketplaces.json",
            install_location=checkout_root,
        )
        rpm_manifest = _write_rpm_manifest(tmp_path / "runtime" / "rpm" / "manifest.json")
        installed_bundle = _write_plugin_bundle(tmp_path / "runtime" / "rpm" / "plugin_123", version="3.5.23")
        mounted_bundle = _write_plugin_bundle(tmp_path / "session" / ".remote-plugins" / "plugin_123", version="3.5.22")
        settings_json = _write_settings(tmp_path / "cowork_settings.json", enable_marketplace=True)

        report = inspect_cowork_runtime_state(
            marketplace_json=marketplace_json,
            known_marketplaces_json=known_marketplaces,
            rpm_manifest=rpm_manifest,
            installed_bundle=installed_bundle,
            session_bundle=mounted_bundle,
            cowork_settings=settings_json,
        )

        assert report["marketplace"]["version"] == "3.5.23"
        assert report["known_marketplace"]["present"] is True
        assert report["known_marketplace"]["cloneExists"] is True
        assert report["installed_runtime"]["version"] == "3.5.23"
        assert report["mounted_session"]["version"] == "3.5.22"
        assert report["enabled_sources"] == ["secondbrain@secondbrain"]

        errors, warnings = validate_cowork_runtime_state(report)

        assert errors == []
        assert any("mounted session snapshot" in warning for warning in warnings)

    def test_flags_enabled_marketplace_source_without_marketplace_checkout(self, tmp_path: Path):
        marketplace_json = _write_repo(tmp_path / "repo", version="3.5.23") / ".claude-plugin" / "marketplace.json"
        known_marketplaces = _write_known_marketplaces(
            tmp_path / "cowork_plugins" / "known_marketplaces.json",
            include_secondbrain=False,
        )
        settings_json = _write_settings(tmp_path / "cowork_settings.json", enable_marketplace=True)

        report = inspect_cowork_runtime_state(
            marketplace_json=marketplace_json,
            known_marketplaces_json=known_marketplaces,
            cowork_settings=settings_json,
        )

        errors, warnings = validate_cowork_runtime_state(report)

        assert warnings == []
        assert any("missing from Cowork known_marketplaces" in error for error in errors)

    def test_flags_wrong_marketplace_repo(self, tmp_path: Path):
        marketplace_json = _write_repo(tmp_path / "repo", version="3.5.23") / ".claude-plugin" / "marketplace.json"
        checkout_root = tmp_path / "cowork_plugins" / "marketplaces" / "secondbrain"
        (checkout_root / ".git").mkdir(parents=True)
        known_marketplaces = _write_known_marketplaces(
            tmp_path / "cowork_plugins" / "known_marketplaces.json",
            install_location=checkout_root,
            repo="someone-else/secondbrain",
        )
        settings_json = _write_settings(tmp_path / "cowork_settings.json", enable_marketplace=True)

        report = inspect_cowork_runtime_state(
            marketplace_json=marketplace_json,
            known_marketplaces_json=known_marketplaces,
            cowork_settings=settings_json,
        )

        errors, warnings = validate_cowork_runtime_state(report)

        assert warnings == []
        assert any("stjepanvrbic/secondbrain" in error for error in errors)

    def test_flags_legacy_upload_source_if_enabled(self, tmp_path: Path):
        marketplace_json = _write_repo(tmp_path / "repo", version="3.5.23") / ".claude-plugin" / "marketplace.json"
        settings_json = _write_settings(
            tmp_path / "cowork_settings.json",
            enable_marketplace=False,
            enable_uploads=True,
        )

        report = inspect_cowork_runtime_state(
            marketplace_json=marketplace_json,
            cowork_settings=settings_json,
        )

        errors, warnings = validate_cowork_runtime_state(report)

        assert warnings == []
        assert any("My Uploads" in error for error in errors)


class TestMain:
    def test_main_returns_zero_for_valid_repo_layout(self, tmp_path: Path):
        repo_root = _write_repo(tmp_path, version="3.5.25")

        assert main(["--repo-root", str(repo_root)]) == 0

    def test_main_returns_nonzero_for_invalid_repo_layout(self, tmp_path: Path):
        repo_root = _write_repo(tmp_path, version="3.5.25")
        (repo_root / "secondbrain" / ".claude-plugin" / "release.json").write_text("{}")

        assert main(["--repo-root", str(repo_root)]) == 1
