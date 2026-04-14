#!/usr/bin/env python3
"""Validate the shipped GitHub-marketplace layout and Cowork runtime state."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
MARKETPLACE_MANIFEST = ".claude-plugin/marketplace.json"
PLUGIN_MANIFEST = "secondbrain/.claude-plugin/plugin.json"
LEGACY_RELEASE_MANIFEST = "secondbrain/.claude-plugin/release.json"
SKILLS_DIR = "secondbrain/skills"
SKILL_VERSION_RE = re.compile(r'^\s*version:\s*"(.*?)"', re.MULTILINE)
CANONICAL_SOURCE = "secondbrain@secondbrain"
LEGACY_UPLOADS_SOURCE = "secondbrain@My Uploads"
EXPECTED_GITHUB_REPO = "stjepanvrbic/secondbrain"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _extract_skill_version(path: Path) -> str | None:
    match = SKILL_VERSION_RE.search(path.read_text())
    return match.group(1) if match else None


def _parse_semver(version: str | None) -> tuple[int, int, int] | None:
    if not isinstance(version, str):
        return None
    parts = version.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def _runtime_identity(bundle_root: Path) -> dict[str, Any]:
    plugin_path = bundle_root / ".claude-plugin" / "plugin.json"
    errors: list[str] = []
    plugin_data: dict[str, Any] = {}

    if not plugin_path.is_file():
        errors.append(f"missing plugin manifest: {plugin_path}")
    else:
        plugin_data = _read_json(plugin_path)

    return {
        "path": str(bundle_root),
        "version": plugin_data.get("version"),
        "pluginId": bundle_root.name,
        "errors": errors,
    }


def validate_marketplace_layout(repo_root: Path) -> list[str]:
    errors: list[str] = []

    marketplace_path = repo_root / MARKETPLACE_MANIFEST
    plugin_path = repo_root / PLUGIN_MANIFEST
    legacy_release_path = repo_root / LEGACY_RELEASE_MANIFEST
    skills_root = repo_root / SKILLS_DIR

    if not marketplace_path.is_file():
        errors.append(f"missing marketplace manifest: {marketplace_path}")
        return errors

    marketplace_data = _read_json(marketplace_path)
    metadata_version = marketplace_data.get("metadata", {}).get("version")
    plugins = marketplace_data.get("plugins", [])

    if not plugin_path.is_file():
        errors.append(f"missing plugin manifest: {plugin_path}")
        return errors

    plugin_data = _read_json(plugin_path)
    plugin_version = plugin_data.get("version")

    if legacy_release_path.exists():
        errors.append(
            f"legacy release manifest must not ship anymore: {legacy_release_path}"
        )

    if metadata_version != plugin_version:
        errors.append(
            f"marketplace metadata.version {metadata_version!r} does not match plugin version {plugin_version!r}"
        )

    if not plugins:
        errors.append("marketplace.json must contain at least one plugin entry")
        return errors

    for plugin in plugins:
        if plugin.get("version") != plugin_version:
            errors.append(
                f"marketplace plugin version {plugin.get('version')!r} does not match plugin version {plugin_version!r}"
            )
        source = plugin.get("source")
        if not isinstance(source, str):
            errors.append(f"marketplace plugin source must be a relative path string, got {source!r}")
            continue
        source_root = (repo_root / source).resolve()
        if not source_root.is_dir():
            errors.append(f"marketplace plugin source path does not exist: {source_root}")
            continue
        if not (source_root / ".claude-plugin" / "plugin.json").is_file():
            errors.append(
                f"marketplace plugin source is missing .claude-plugin/plugin.json: {source_root}"
            )

    if skills_root.is_dir():
        for skill_path in sorted(skills_root.glob("*/SKILL.md")):
            skill_version = _extract_skill_version(skill_path)
            if skill_version != plugin_version:
                errors.append(
                    f"{skill_path.relative_to(repo_root)} metadata.version {skill_version!r} does not match plugin version {plugin_version!r}"
                )

    return errors


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
    known_marketplaces_json: Path | None = None,
    rpm_manifest: Path | None = None,
    installed_bundle: Path | None = None,
    session_bundle: Path | None = None,
    cowork_settings: Path | None = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "marketplace": None,
        "known_marketplace": None,
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

    if known_marketplaces_json is not None:
        known_data = _read_json(known_marketplaces_json)
        entry = known_data.get("secondbrain")
        install_location_value = entry.get("installLocation") if isinstance(entry, dict) else None
        install_location = Path(install_location_value) if isinstance(install_location_value, str) else None
        source = entry.get("source", {}) if isinstance(entry, dict) else {}
        report["known_marketplace"] = {
            "path": str(known_marketplaces_json),
            "present": bool(entry),
            "sourceType": source.get("source") if isinstance(source, dict) else None,
            "repo": source.get("repo") if isinstance(source, dict) else None,
            "installLocation": str(install_location) if install_location is not None else None,
            "cloneExists": bool(
                install_location is not None
                and install_location.is_dir()
                and (install_location / ".git").exists()
            ),
        }

    if rpm_manifest is not None:
        rpm_data = _read_json(rpm_manifest)
        plugin_record = next(
            (plugin for plugin in rpm_data.get("plugins", []) if plugin.get("name") == "secondbrain"),
            None,
        )
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
            source_name
            for source_name, enabled in enabled_plugins.items()
            if enabled and source_name.startswith("secondbrain@")
        )

    return report


def validate_cowork_runtime_state(report: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    marketplace = report.get("marketplace") or {}
    known_marketplace = report.get("known_marketplace") or {}
    rpm = report.get("rpm") or {}
    installed = report.get("installed_runtime") or {}
    mounted = report.get("mounted_session") or {}
    enabled_sources = report.get("enabled_sources") or []

    marketplace_version = marketplace.get("version")
    metadata_version = marketplace.get("metadataVersion")
    if marketplace and metadata_version != marketplace_version:
        errors.append(
            f"marketplace metadata.version {metadata_version!r} does not match plugin version {marketplace_version!r}"
        )

    if CANONICAL_SOURCE in enabled_sources:
        if not known_marketplace or not known_marketplace.get("present"):
            errors.append(
                "secondbrain@secondbrain is enabled but secondbrain is missing from Cowork known_marketplaces.json"
            )
        else:
            if known_marketplace.get("sourceType") != "github":
                errors.append(
                    f"Cowork marketplace source must be GitHub-backed from {EXPECTED_GITHUB_REPO}"
                )
            if known_marketplace.get("repo") != EXPECTED_GITHUB_REPO:
                errors.append(
                    f"Cowork marketplace repo must be {EXPECTED_GITHUB_REPO}, got {known_marketplace.get('repo')!r}"
                )
            if not known_marketplace.get("cloneExists"):
                errors.append(
                    f"Cowork marketplace checkout is missing its local clone at {known_marketplace.get('installLocation')!r}"
                )

    if LEGACY_UPLOADS_SOURCE in enabled_sources:
        errors.append(
            "legacy Cowork upload source is enabled. Disable My Uploads and install secondbrain from the GitHub marketplace instead."
        )

    if rpm and rpm.get("plugin") is None:
        errors.append("rpm manifest does not include secondbrain in the installed plugin set")

    if installed:
        errors.extend(installed.get("errors", []))
        installed_version = installed.get("version")
        if marketplace and installed_version != marketplace_version:
            errors.append(
                f"installed runtime version {installed_version!r} does not match marketplace version {marketplace_version!r}"
            )

    if mounted:
        errors.extend(mounted.get("errors", []))
        installed_tuple = _parse_semver(installed.get("version"))
        mounted_tuple = _parse_semver(mounted.get("version"))
        if installed_tuple and mounted_tuple:
            if mounted_tuple < installed_tuple:
                warnings.append(
                    f"mounted session snapshot {mounted.get('version')} is older than installed runtime {installed.get('version')}; start a fresh Cowork session to pick up the update"
                )
            elif mounted_tuple > installed_tuple:
                errors.append(
                    f"mounted session snapshot {mounted.get('version')} is newer than installed runtime {installed.get('version')}"
                )

    return errors, warnings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="validate_distribution.py")
    parser.add_argument("--repo-root", default=str(REPO_ROOT), help="Repo root to validate")
    parser.add_argument("--claude-smoke", action="store_true", help="Run a temp-HOME Claude CLI marketplace smoke test")
    parser.add_argument("--claude-bin", default="claude", help="Claude CLI binary to use for smoke tests")
    parser.add_argument("--marketplace-json", help="Path to marketplace.json for Cowork runtime validation")
    parser.add_argument("--cowork-known-marketplaces", help="Path to Cowork known_marketplaces.json")
    parser.add_argument("--cowork-rpm-manifest", help="Path to Cowork rpm/manifest.json")
    parser.add_argument("--cowork-installed-bundle", help="Path to installed Cowork runtime bundle root")
    parser.add_argument("--cowork-session-bundle", help="Path to mounted session bundle root")
    parser.add_argument("--cowork-settings", help="Path to Cowork settings JSON")
    parser.add_argument("--report-json", action="store_true", help="Print the computed Cowork runtime report as JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path(args.repo_root).resolve()

    layout_errors = validate_marketplace_layout(repo_root)
    if layout_errors:
        for error in layout_errors:
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
            args.cowork_known_marketplaces,
            args.cowork_rpm_manifest,
            args.cowork_installed_bundle,
            args.cowork_session_bundle,
            args.cowork_settings,
        ]
    )
    if runtime_args_present:
        report = inspect_cowork_runtime_state(
            marketplace_json=Path(args.marketplace_json).resolve() if args.marketplace_json else None,
            known_marketplaces_json=Path(args.cowork_known_marketplaces).resolve()
            if args.cowork_known_marketplaces
            else None,
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
