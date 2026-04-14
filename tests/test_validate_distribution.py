"""Tests for release/distribution validation helpers."""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "secondbrain" / "scripts"))

from validate_distribution import (  # type: ignore[reportMissingImports]
    inspect_cowork_runtime_state,
    main,
    validate_cowork_runtime_state,
    validate_release_zip,
)


def _write_zip(tmp_path: Path, members: dict[str, str]) -> Path:
    zip_path = tmp_path / "secondbrain.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    return zip_path


def _write_bundle(root: Path, *, version: str, git_commit: str) -> Path:
    manifest_dir = root / ".claude-plugin"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "plugin.json").write_text(
        json.dumps({"name": "secondbrain", "version": version})
    )
    (manifest_dir / "release.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "pluginVersion": version,
                "gitTag": f"v{version}",
                "gitCommit": git_commit,
                "releaseAssetName": f"secondbrain-v{version}.zip",
            }
        )
    )
    return root


def _write_marketplace_json(path: Path, *, version: str) -> Path:
    path.write_text(
        json.dumps(
            {
                "metadata": {"version": version},
                "plugins": [{"name": "secondbrain", "version": version, "source": "./secondbrain"}],
            }
        )
    )
    return path


def _write_rpm_manifest(path: Path, *, plugin_id: str = "plugin_123") -> Path:
    path.parent.mkdir(parents=True)
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


def _write_settings(path: Path, *, include_uploads: bool) -> Path:
    enabled_plugins = {
        "secondbrain@secondbrain": True,
    }
    if include_uploads:
        enabled_plugins["secondbrain@My Uploads"] = True
    path.write_text(json.dumps({"enabledPlugins": enabled_plugins}))
    return path


class TestValidateReleaseZip:
    def test_accepts_valid_shipped_layout(self, tmp_path: Path):
        zip_path = _write_zip(
            tmp_path,
            {
                "secondbrain/.claude-plugin/plugin.json": '{"name":"secondbrain","version":"3.5.18"}',
                "secondbrain/.claude-plugin/release.json": '{"schemaVersion":1,"pluginVersion":"3.5.18","gitTag":"v3.5.18","gitCommit":"abc123","releaseAssetName":"secondbrain-v3.5.18.zip"}',
                "secondbrain/skills/init/SKILL.md": "# init\n",
                "secondbrain/scripts/init_obsidian.py": "print('ok')\n",
            },
        )

        assert validate_release_zip(zip_path) == []

    def test_rejects_missing_release_manifest(self, tmp_path: Path):
        zip_path = _write_zip(
            tmp_path,
            {
                "secondbrain/.claude-plugin/plugin.json": '{"name":"secondbrain","version":"3.5.18"}',
                "secondbrain/skills/init/SKILL.md": "# init\n",
            },
        )

        errors = validate_release_zip(zip_path)

        assert any("release.json" in error for error in errors)

    def test_rejects_mismatched_release_manifest_version(self, tmp_path: Path):
        zip_path = _write_zip(
            tmp_path,
            {
                "secondbrain/.claude-plugin/plugin.json": '{"name":"secondbrain","version":"3.5.18"}',
                "secondbrain/.claude-plugin/release.json": '{"schemaVersion":1,"pluginVersion":"3.5.17","gitTag":"v3.5.17","gitCommit":"abc123","releaseAssetName":"secondbrain-v3.5.17.zip"}',
            },
        )

        errors = validate_release_zip(zip_path)

        assert any("pluginVersion" in error for error in errors)

    def test_rejects_unprefixed_repo_root_files(self, tmp_path: Path):
        zip_path = _write_zip(
            tmp_path,
            {
                "README.md": "# repo root leak\n",
                "secondbrain/.claude-plugin/plugin.json": '{"name":"secondbrain","version":"3.5.18"}',
                "secondbrain/.claude-plugin/release.json": '{"schemaVersion":1,"pluginVersion":"3.5.18","gitTag":"v3.5.18","gitCommit":"abc123","releaseAssetName":"secondbrain-v3.5.18.zip"}',
            },
        )

        errors = validate_release_zip(zip_path)

        assert any("must be under secondbrain/" in error for error in errors)

    def test_rejects_tests_in_release_zip(self, tmp_path: Path):
        zip_path = _write_zip(
            tmp_path,
            {
                "secondbrain/.claude-plugin/plugin.json": '{"name":"secondbrain","version":"3.5.18"}',
                "secondbrain/.claude-plugin/release.json": '{"schemaVersion":1,"pluginVersion":"3.5.18","gitTag":"v3.5.18","gitCommit":"abc123","releaseAssetName":"secondbrain-v3.5.18.zip"}',
                "secondbrain/tests/test_something.py": "def test_nope(): pass\n",
            },
        )

        errors = validate_release_zip(zip_path)

        assert any("must not ship tests/" in error for error in errors)


class TestCoworkRuntimeState:
    def test_reports_marketplace_installed_and_session_versions_separately(self, tmp_path: Path):
        marketplace_json = _write_marketplace_json(tmp_path / "marketplace.json", version="3.5.23")
        rpm_manifest = _write_rpm_manifest(tmp_path / "runtime" / "rpm" / "manifest.json")
        installed_bundle = _write_bundle(tmp_path / "runtime" / "rpm" / "plugin_123", version="3.5.23", git_commit="installed-sha")
        mounted_bundle = _write_bundle(tmp_path / "session" / ".remote-plugins" / "plugin_123", version="3.5.22", git_commit="session-sha")
        settings_json = _write_settings(tmp_path / "cowork_settings.json", include_uploads=True)

        report = inspect_cowork_runtime_state(
            marketplace_json=marketplace_json,
            rpm_manifest=rpm_manifest,
            installed_bundle=installed_bundle,
            session_bundle=mounted_bundle,
            cowork_settings=settings_json,
        )

        assert report["marketplace"]["version"] == "3.5.23"
        assert report["installed_runtime"]["version"] == "3.5.23"
        assert report["mounted_session"]["version"] == "3.5.22"
        assert report["enabled_sources"] == ["secondbrain@My Uploads", "secondbrain@secondbrain"]

        errors, warnings = validate_cowork_runtime_state(report)

        assert errors == []
        assert any("mounted session" in warning for warning in warnings)
        assert any("My Uploads" in warning for warning in warnings)

    def test_flags_installed_runtime_that_did_not_advance_to_marketplace_version(self, tmp_path: Path):
        marketplace_json = _write_marketplace_json(tmp_path / "marketplace.json", version="3.5.23")
        rpm_manifest = _write_rpm_manifest(tmp_path / "runtime" / "rpm" / "manifest.json")
        installed_bundle = _write_bundle(tmp_path / "runtime" / "rpm" / "plugin_123", version="3.5.22", git_commit="installed-sha")
        settings_json = _write_settings(tmp_path / "cowork_settings.json", include_uploads=False)

        report = inspect_cowork_runtime_state(
            marketplace_json=marketplace_json,
            rpm_manifest=rpm_manifest,
            installed_bundle=installed_bundle,
            cowork_settings=settings_json,
        )

        errors, warnings = validate_cowork_runtime_state(report)

        assert warnings == []
        assert any("installed runtime" in error for error in errors)


class TestMain:
    def test_main_returns_zero_for_valid_zip(self, tmp_path: Path):
        zip_path = _write_zip(
            tmp_path,
            {
                "secondbrain/.claude-plugin/plugin.json": '{"name":"secondbrain","version":"3.5.18"}',
                "secondbrain/.claude-plugin/release.json": '{"schemaVersion":1,"pluginVersion":"3.5.18","gitTag":"v3.5.18","gitCommit":"abc123","releaseAssetName":"secondbrain-v3.5.18.zip"}',
            },
        )

        assert main(["--zip-path", str(zip_path)]) == 0

    def test_main_returns_nonzero_for_invalid_zip(self, tmp_path: Path):
        zip_path = _write_zip(
            tmp_path,
            {
                "README.md": "# broken\n",
            },
        )

        assert main(["--zip-path", str(zip_path)]) == 1
