#!/usr/bin/env python3
"""
bump_version.py — Bump version across all shipped version-managed files.

Ensures plugin.json, marketplace.json, release.json, and all SKILL.md
frontmatter stay in sync. Bumps patch by default, or accepts explicit version.

Usage:
    python3 bump_version.py                    # auto bump patch
    python3 bump_version.py 3.1.0             # set explicit version
    python3 bump_version.py --check           # verify all versions match (exit 1 if not)
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, List, Optional, Tuple

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = PLUGIN_ROOT.parent
RELEASE_SCHEMA_VERSION = 1

VERSION_FILES = {
    "plugin.json": PLUGIN_ROOT / ".claude-plugin" / "plugin.json",
    "marketplace.json": REPO_ROOT / ".claude-plugin" / "marketplace.json",
    "release.json": PLUGIN_ROOT / ".claude-plugin" / "release.json",
}

SKILLS_DIR = PLUGIN_ROOT / "skills"
SKILL_VERSION_RE = re.compile(r'^(\s*version:\s*")(.*?)(")', re.MULTILINE)


def read_current_version() -> str:
    data = json.loads(VERSION_FILES["marketplace.json"].read_text())
    plugins = data.get("plugins", [])
    if not plugins:
        raise KeyError("marketplace.json has no plugins entry")
    return plugins[0]["version"]


def parse_version(v: str) -> Tuple[int, int, int]:
    major, minor, patch = v.split(".")
    return int(major), int(minor), int(patch)


def bump_patch(v: str) -> str:
    major, minor, patch = parse_version(v)
    return f"{major}.{minor}.{patch + 1}"


def release_tag(version: str) -> str:
    return f"v{version}"


def release_asset_name(version: str) -> str:
    return f"secondbrain-v{version}.zip"


def release_manifest_defaults(version: str) -> dict[str, Any]:
    return {
        "schemaVersion": RELEASE_SCHEMA_VERSION,
        "pluginVersion": version,
        "gitTag": release_tag(version),
        "gitCommit": "",
        "releaseAssetName": release_asset_name(version),
    }


def find_skill_files() -> List[Path]:
    return sorted(SKILLS_DIR.glob("*/SKILL.md"))


def load_release_manifest() -> dict[str, Any]:
    path = VERSION_FILES["release.json"]
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def get_all_versions() -> List[Tuple[str, str, Path]]:
    versions: List[Tuple[str, str, Path]] = []

    plugin_path = VERSION_FILES["plugin.json"]
    plugin_data = json.loads(plugin_path.read_text())
    versions.append(("plugin.json (version)", plugin_data.get("version", ""), plugin_path))

    marketplace_path = VERSION_FILES["marketplace.json"]
    marketplace_data = json.loads(marketplace_path.read_text())
    metadata_version = marketplace_data.get("metadata", {}).get("version", "")
    if metadata_version:
        versions.append(("marketplace.json (metadata.version)", metadata_version, marketplace_path))
    for plugin in marketplace_data.get("plugins", []):
        versions.append(("marketplace.json (plugins[].version)", plugin.get("version", ""), marketplace_path))

    release_path = VERSION_FILES["release.json"]
    release_data = json.loads(release_path.read_text())
    versions.append(("release.json (pluginVersion)", release_data.get("pluginVersion", ""), release_path))

    for skill_path in find_skill_files():
        text = skill_path.read_text()
        match = SKILL_VERSION_RE.search(text)
        if match:
            versions.append((f"skills/{skill_path.parent.name}/SKILL.md", match.group(2), skill_path))

    return versions


def check_consistency() -> Tuple[bool, List[str]]:
    versions = get_all_versions()
    if not versions:
        return False, ["No version files found"]

    canonical = versions[0][1]
    mismatches: list[str] = []
    for name, version, _ in versions:
        if version != canonical:
            mismatches.append(f"  {name}: {version} (expected {canonical})")

    if mismatches:
        return False, [f"Version mismatch (canonical: {canonical}):"] + mismatches
    return True, [f"All {len(versions)} version references are {canonical}"]


def set_version(
    new_version: str,
    *,
    git_tag: str | None = None,
    git_commit: str | None = None,
    release_asset_name: str | None = None,
) -> int:
    changed = 0

    plugin_path = VERSION_FILES["plugin.json"]
    plugin_data = json.loads(plugin_path.read_text())
    if plugin_data.get("version") != new_version:
        plugin_data["version"] = new_version
        plugin_path.write_text(json.dumps(plugin_data, indent=2) + "\n")
        changed += 1

    marketplace_path = VERSION_FILES["marketplace.json"]
    marketplace_data = json.loads(marketplace_path.read_text())
    metadata = marketplace_data.setdefault("metadata", {})
    if metadata.get("version") != new_version:
        metadata["version"] = new_version
        changed += 1
    for plugin in marketplace_data.get("plugins", []):
        if plugin.get("version") != new_version:
            plugin["version"] = new_version
            changed += 1
    marketplace_path.write_text(json.dumps(marketplace_data, indent=2) + "\n")

    release_path = VERSION_FILES["release.json"]
    release_data = load_release_manifest() or {}
    previous_version = release_data.get("pluginVersion")
    desired_defaults = release_manifest_defaults(new_version)
    desired_git_tag = git_tag if git_tag is not None else desired_defaults["gitTag"]
    desired_asset = release_asset_name if release_asset_name is not None else desired_defaults["releaseAssetName"]
    if git_commit is not None:
        desired_commit = git_commit
    elif previous_version != new_version:
        desired_commit = ""
    else:
        desired_commit = release_data.get("gitCommit", desired_defaults["gitCommit"])

    desired_release = {
        **release_data,
        "schemaVersion": RELEASE_SCHEMA_VERSION,
        "pluginVersion": new_version,
        "gitTag": desired_git_tag,
        "gitCommit": desired_commit,
        "releaseAssetName": desired_asset,
    }

    release_changed_fields = 0
    for key, value in desired_release.items():
        if release_data.get(key) != value:
            release_changed_fields += 1
    if release_changed_fields:
        release_path.write_text(json.dumps(desired_release, indent=2) + "\n")
        changed += release_changed_fields

    for skill_path in find_skill_files():
        text = skill_path.read_text()
        new_text = SKILL_VERSION_RE.sub(rf'\g<1>{new_version}\3', text)
        if new_text != text:
            skill_path.write_text(new_text)
            changed += 1

    return changed


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True,
        text=True,
        check=True,
    )


def version_managed_paths() -> List[Path]:
    paths = [
        VERSION_FILES["plugin.json"],
        VERSION_FILES["marketplace.json"],
        VERSION_FILES["release.json"],
    ]
    paths.extend(find_skill_files())
    return paths


def assert_release_tree_clean() -> bool:
    return _git("status", "--porcelain").stdout.strip() == ""


def create_tag(version: str) -> int:
    tag = release_tag(version)
    status = _git("status", "--porcelain").stdout.strip()
    if status:
        print("error: working tree is dirty — commit or stash first", file=sys.stderr)
        return 1

    ok, messages = check_consistency()
    if not ok:
        for msg in messages:
            print(msg, file=sys.stderr)
        return 1

    existing = _git("tag", "-l", tag).stdout.strip()
    if existing:
        print(f"error: tag {tag} already exists", file=sys.stderr)
        return 1

    _git("tag", "-a", tag, "-m", tag)
    print(f"Created tag {tag}")
    return 0


def release(new_version: Optional[str] = None) -> int:
    current = read_current_version()
    if new_version is None:
        new_version = bump_patch(current)

    if not assert_release_tree_clean():
        print(
            "error: working tree is dirty — release automation refuses to stage unrelated changes",
            file=sys.stderr,
        )
        return 1

    print(f"{current} -> {new_version}")
    changed = set_version(new_version)
    print(f"Updated {changed} files")

    ok, messages = check_consistency()
    for msg in messages:
        print(msg)
    if not ok:
        return 1

    stage_paths = [str(path.relative_to(REPO_ROOT)) for path in version_managed_paths()]
    _git("add", "--", *stage_paths)
    _git("commit", "-m", f"Bump to {new_version}")
    print("Committed version bump")

    rc = create_tag(new_version)
    if rc != 0:
        return rc

    print(f"\nReleased v{new_version}. Push with: git push")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])

    if "--check" in args:
        ok, messages = check_consistency()
        for msg in messages:
            print(msg)
        return 0 if ok else 1

    if "--tag" in args:
        return create_tag(read_current_version())

    if "--current" in args:
        print(read_current_version())
        return 0

    git_tag_arg: str | None = None
    git_commit_arg: str | None = None
    release_asset_arg: str | None = None

    cleaned_args: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--git-tag":
            git_tag_arg = args[i + 1]
            i += 2
            continue
        if arg == "--git-commit":
            git_commit_arg = args[i + 1]
            i += 2
            continue
        if arg == "--release-asset":
            release_asset_arg = args[i + 1]
            i += 2
            continue
        cleaned_args.append(arg)
        i += 1
    args = cleaned_args

    explicit_version = next((arg for arg in args if not arg.startswith("-")), None)

    if "--release" in args:
        return release(explicit_version)

    current = read_current_version()
    new_version = explicit_version if explicit_version else bump_patch(current)
    print(f"{current} -> {new_version}")
    changed = set_version(
        new_version,
        git_tag=git_tag_arg,
        git_commit=git_commit_arg,
        release_asset_name=release_asset_arg,
    )
    print(f"Updated {changed} files")

    ok, messages = check_consistency()
    for msg in messages:
        print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
