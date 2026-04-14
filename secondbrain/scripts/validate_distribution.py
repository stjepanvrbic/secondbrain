#!/usr/bin/env python3
"""Validate shipped release assets and Cowork runtime/update state."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
ZIP_PREFIX = "secondbrain/"
PLUGIN_MANIFEST = f"{ZIP_PREFIX}.claude-plugin/plugin.json"
RELEASE_MANIFEST = f"{ZIP_PREFIX}.claude-plugin/release.json"
FORBIDDEN_SEGMENTS = {"__pycache__"}
FORBIDDEN_SUFFIXES = (".pyc",)
FORBIDDEN_BASENAMES = {".DS_Store"}
CANONICAL_SOURCE = "secondbrain@secondbrain"
UPLOADS_SOURCE = "secondbrain@My Uploads"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _release_asset_name(version: str) -> str:
    return f"secondbrain-v{version}.zip"


def _runtime_identity(bundle_root: Path) -> dict[str, Any]:
    plugin_path = bundle_root / ".claude-plugin" / "plugin.json"
    release_path = bundle_root / ".claude-plugin" / "release.json"
    errors: list[str] = []

    plugin_data: dict[str, Any] = {}
    release_data: dict[str, Any] = {}
    if not plugin_path.is_file():
        errors.append(f"missing plugin manifest: {plugin_path}")
    else:
        plugin_data = _read_json(plugin_path)
    if not release_path.is_file():
        errors.append(f"missing release manifest: {release_path}")
    else:
        release_data = _read_json(release_path)

    return {
        "path": str(bundle_root),
        "version": plugin_data.get("version"),
        "gitTag": release_data.get("gitTag"),
        "gitCommit": release_data.get("gitCommit"),
        "releaseAssetName": release_data.get("releaseAssetName"),
        "pluginId": bundle_root.name,
        "errors": errors,
    }


def validate_release_zip(
    zip_path: Path,
    *,
    expected_tag: str | None = None,
    expected_asset_name: str | None = None,
) -> list[str]:
    errors: list[str] = []

    if not zip_path.is_file():
        return [f"release ZIP does not exist: {zip_path}"]

    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = [name for name in zf.namelist() if not name.endswith("/")]
            name_set = set(names)

            if not names:
                errors.append("release ZIP is empty")
                return errors

            for name in names:
                if not name.startswith(ZIP_PREFIX):
                    errors.append(f"release ZIP entries must be under {ZIP_PREFIX}: found {name}")
                parts = Path(name).parts
                if any(segment in FORBIDDEN_SEGMENTS for segment in parts):
                    errors.append(f"release ZIP must not ship __pycache__ directories: {name}")
                if name.endswith(FORBIDDEN_SUFFIXES):
                    errors.append(f"release ZIP must not ship compiled Python files: {name}")
                if Path(name).name in FORBIDDEN_BASENAMES:
                    errors.append(f"release ZIP must not ship macOS metadata files: {name}")
                if name.startswith(f"{ZIP_PREFIX}tests/"):
                    errors.append(f"release ZIP must not ship tests/: {name}")

            if PLUGIN_MANIFEST not in name_set:
                errors.append(f"release ZIP is missing required manifest: {PLUGIN_MANIFEST}")
                return errors
            if RELEASE_MANIFEST not in name_set:
                errors.append(f"release ZIP is missing required manifest: {RELEASE_MANIFEST}")
                return errors

            try:
                plugin_data = json.loads(zf.read(PLUGIN_MANIFEST))
                release_data = json.loads(zf.read(RELEASE_MANIFEST))
            except (json.JSONDecodeError, KeyError, UnicodeDecodeError) as exc:
                errors.append(f"cannot read release manifests: {exc}")
                return errors

            if plugin_data.get("name") != "secondbrain":
                errors.append("release plugin.json must declare name 'secondbrain'")

            version = plugin_data.get("version")
            if not isinstance(version, str) or version.count(".") != 2:
                errors.append("release plugin.json must contain a semver version")
                return errors

            if release_data.get("schemaVersion") != 1:
                errors.append("release.json must declare schemaVersion 1")
            if release_data.get("pluginVersion") != version:
                errors.append(
                    f"release.json pluginVersion {release_data.get('pluginVersion')!r} must match plugin.json version {version!r}"
                )
            expected_release_tag = f"v{version}"
            if release_data.get("gitTag") != expected_release_tag:
                errors.append(
                    f"release.json gitTag {release_data.get('gitTag')!r} must match {expected_release_tag!r}"
                )
            expected_asset = _release_asset_name(version)
            if release_data.get("releaseAssetName") != expected_asset:
                errors.append(
                    f"release.json releaseAssetName {release_data.get('releaseAssetName')!r} must match {expected_asset!r}"
                )
            if expected_tag is not None and release_data.get("gitTag") != expected_tag:
                errors.append(
                    f"release.json gitTag {release_data.get('gitTag')!r} must match expected tag {expected_tag!r}"
                )
            if expected_asset_name is not None and release_data.get("releaseAssetName") != expected_asset_name:
                errors.append(
                    f"release.json releaseAssetName {release_data.get('releaseAssetName')!r} must match expected asset {expected_asset_name!r}"
                )
    except zipfile.BadZipFile as exc:
        errors.append(f"invalid ZIP file: {exc}")

    return errors


def build_release_zip(repo_root: Path, output_path: Path) -> None:
    subprocess.run(
        [
            "git",
            "-C",
            str(repo_root),
            "archive",
            "--format=zip",
            "--prefix=secondbrain/",
            "-o",
            str(output_path),
            "HEAD:secondbrain",
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def run_claude_marketplace_smoke(repo_root: Path, claude_bin: str) -> None:
    if shutil.which(claude_bin) is None:
        raise RuntimeError(f"{claude_bin!r} is not on PATH")

    with tempfile.TemporaryDirectory() as tmpdir:
        env = os.environ.copy()
        env["HOME"] = tmpdir

        commands: Sequence[list[str]] = (
            [claude_bin, "plugins", "marketplace", "add", str(repo_root)],
            [claude_bin, "plugins", "install", CANONICAL_SOURCE],
            [claude_bin, "plugins", "update", CANONICAL_SOURCE],
        )

        for command in commands:
            subprocess.run(command, check=True, env=env, capture_output=True, text=True)


def inspect_cowork_runtime_state(
    *,
    marketplace_json: Path | None = None,
    rpm_manifest: Path | None = None,
    installed_bundle: Path | None = None,
    session_bundle: Path | None = None,
    cowork_settings: Path | None = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "marketplace": None,
        "rpm": None,
        "installed_runtime": None,
        "mounted_session": None,
        "enabled_sources": [],
    }

    if marketplace_json is not None:
        marketplace_data = _read_json(marketplace_json)
        plugins = marketplace_data.get("plugins", [])
        plugin_version = plugins[0].get("version") if plugins else None
        report["marketplace"] = {
            "path": str(marketplace_json),
            "version": plugin_version,
            "metadataVersion": marketplace_data.get("metadata", {}).get("version"),
        }

    if rpm_manifest is not None:
        rpm_data = _read_json(rpm_manifest)
        plugin_record = next((plugin for plugin in rpm_data.get("plugins", []) if plugin.get("name") == "secondbrain"), None)
        report["rpm"] = {
            "path": str(rpm_manifest),
            "plugin": plugin_record,
        }

    if installed_bundle is not None:
        report["installed_runtime"] = _runtime_identity(installed_bundle)

    if session_bundle is not None:
        report["mounted_session"] = _runtime_identity(session_bundle)

    if cowork_settings is not None:
        settings_data = _read_json(cowork_settings)
        enabled_plugins = settings_data.get("enabledPlugins", {})
        report["enabled_sources"] = sorted(
            name
            for name, enabled in enabled_plugins.items()
            if enabled and name.startswith("secondbrain@")
        )

    return report


def validate_cowork_runtime_state(report: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    marketplace = report.get("marketplace") or {}
    installed = report.get("installed_runtime") or {}
    mounted = report.get("mounted_session") or {}
    rpm = report.get("rpm") or {}
    enabled_sources = report.get("enabled_sources") or []

    if marketplace:
        version = marketplace.get("version")
        metadata_version = marketplace.get("metadataVersion")
        if metadata_version is not None and metadata_version != version:
            errors.append(
                f"marketplace metadata.version {metadata_version!r} does not match plugin version {version!r}"
            )

    if rpm and rpm.get("plugin") is None:
        errors.append("rpm manifest does not include secondbrain in the installed plugin set")

    if installed:
        errors.extend(installed.get("errors", []))
        installed_version = installed.get("version")
        if marketplace and installed_version != marketplace.get("version"):
            errors.append(
                f"installed runtime version {installed_version!r} does not match marketplace version {marketplace.get('version')!r}"
            )
        if installed_version is not None:
            expected_tag = f"v{installed_version}"
            expected_asset = _release_asset_name(installed_version)
            if installed.get("gitTag") != expected_tag:
                errors.append(
                    f"installed runtime gitTag {installed.get('gitTag')!r} does not match {expected_tag!r}"
                )
            if installed.get("releaseAssetName") != expected_asset:
                errors.append(
                    f"installed runtime releaseAssetName {installed.get('releaseAssetName')!r} does not match {expected_asset!r}"
                )

    if mounted:
        warnings_or_errors = mounted.get("errors", [])
        if warnings_or_errors:
            errors.extend(warnings_or_errors)
        installed_version = installed.get("version")
        mounted_version = mounted.get("version")
        if installed_version and mounted_version:
            installed_tuple = tuple(int(part) for part in str(installed_version).split("."))
            mounted_tuple = tuple(int(part) for part in str(mounted_version).split("."))
            if mounted_tuple < installed_tuple:
                warnings.append(
                    f"mounted session snapshot {mounted_version} is older than installed runtime {installed_version}; start a fresh Cowork session to pick up the update"
                )
            elif mounted_tuple > installed_tuple:
                errors.append(
                    f"mounted session snapshot {mounted_version} is newer than installed runtime {installed_version}"
                )

    if CANONICAL_SOURCE in enabled_sources and UPLOADS_SOURCE in enabled_sources:
        warnings.append(
            "multiple secondbrain install sources are enabled (secondbrain@secondbrain and secondbrain@My Uploads); marketplace auto-update is only guaranteed for secondbrain@secondbrain"
        )

    return errors, warnings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="validate_distribution.py")
    parser.add_argument("--repo-root", default=str(REPO_ROOT), help="Repo root to validate/build from")
    parser.add_argument("--zip-path", help="Existing release ZIP to validate. If omitted, build one from HEAD:secondbrain")
    parser.add_argument("--expected-tag", help="Expected git tag embedded in release.json")
    parser.add_argument("--expected-asset-name", help="Expected asset name embedded in release.json")
    parser.add_argument("--claude-smoke", action="store_true", help="Run a local Claude CLI marketplace/add install smoke test in a temp HOME")
    parser.add_argument("--claude-bin", default="claude", help="Claude CLI binary to use for smoke tests")
    parser.add_argument("--marketplace-json", help="Path to marketplace.json for Cowork runtime validation")
    parser.add_argument("--cowork-rpm-manifest", help="Path to Cowork rpm/manifest.json")
    parser.add_argument("--cowork-installed-bundle", help="Path to installed Cowork runtime bundle root")
    parser.add_argument("--cowork-session-bundle", help="Path to mounted session bundle root")
    parser.add_argument("--cowork-settings", help="Path to Cowork settings JSON")
    parser.add_argument("--report-json", action="store_true", help="Print the computed Cowork runtime report as JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path(args.repo_root).resolve()

    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = Path(args.zip_path).resolve() if args.zip_path else Path(tmpdir) / "release.zip"
        if not args.zip_path:
            build_release_zip(repo_root, zip_path)

        errors = validate_release_zip(
            zip_path,
            expected_tag=args.expected_tag,
            expected_asset_name=args.expected_asset_name,
        )
        if errors:
            for error in errors:
                print(error, file=sys.stderr)
            return 1

        if args.claude_smoke:
            try:
                run_claude_marketplace_smoke(repo_root, args.claude_bin)
            except (RuntimeError, subprocess.CalledProcessError) as exc:
                print(f"Claude marketplace smoke test failed: {exc}", file=sys.stderr)
                return 1

    runtime_args_present = any(
        value
        for value in [
            args.marketplace_json,
            args.cowork_rpm_manifest,
            args.cowork_installed_bundle,
            args.cowork_session_bundle,
            args.cowork_settings,
        ]
    )
    if runtime_args_present:
        report = inspect_cowork_runtime_state(
            marketplace_json=Path(args.marketplace_json).resolve() if args.marketplace_json else None,
            rpm_manifest=Path(args.cowork_rpm_manifest).resolve() if args.cowork_rpm_manifest else None,
            installed_bundle=Path(args.cowork_installed_bundle).resolve() if args.cowork_installed_bundle else None,
            session_bundle=Path(args.cowork_session_bundle).resolve() if args.cowork_session_bundle else None,
            cowork_settings=Path(args.cowork_settings).resolve() if args.cowork_settings else None,
        )
        runtime_errors, runtime_warnings = validate_cowork_runtime_state(report)
        if args.report_json:
            print(json.dumps(report, indent=2, sort_keys=True))
        for warning in runtime_warnings:
            print(warning, file=sys.stderr)
        if runtime_errors:
            for error in runtime_errors:
                print(error, file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
