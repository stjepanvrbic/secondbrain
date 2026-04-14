#!/usr/bin/env python3
"""
bump_version.py — keep shipped marketplace versions in lockstep.

The canonical version lives in `secondbrain/.claude-plugin/plugin.json`.
Derived copies live in:
  - `.claude-plugin/marketplace.json` metadata.version
  - `.claude-plugin/marketplace.json` plugins[].version
  - every `secondbrain/skills/*/SKILL.md` metadata.version
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import List, Tuple

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = PLUGIN_ROOT.parent

VERSION_FILES = {
    "plugin.json": PLUGIN_ROOT / ".claude-plugin" / "plugin.json",
    "marketplace.json": REPO_ROOT / ".claude-plugin" / "marketplace.json",
}

SKILLS_DIR = PLUGIN_ROOT / "skills"
SKILL_VERSION_RE = re.compile(r'^(\s*version:\s*")(.*?)(")', re.MULTILINE)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n")


def parse_version(version: str) -> Tuple[int, int, int]:
    major, minor, patch = version.split(".")
    return int(major), int(minor), int(patch)


def bump_patch(version: str) -> str:
    major, minor, patch = parse_version(version)
    return f"{major}.{minor}.{patch + 1}"


def read_current_version() -> str:
    data = read_json(VERSION_FILES["plugin.json"])
    return data["version"]


def find_skill_files() -> List[Path]:
    return sorted(SKILLS_DIR.glob("*/SKILL.md"))


def get_all_versions() -> List[Tuple[str, str, Path]]:
    versions: List[Tuple[str, str, Path]] = []

    plugin_path = VERSION_FILES["plugin.json"]
    plugin_data = read_json(plugin_path)
    versions.append(("plugin.json (version)", plugin_data.get("version", ""), plugin_path))

    marketplace_path = VERSION_FILES["marketplace.json"]
    marketplace_data = read_json(marketplace_path)
    versions.append(
        (
            "marketplace.json (metadata.version)",
            marketplace_data.get("metadata", {}).get("version", ""),
            marketplace_path,
        )
    )
    for plugin in marketplace_data.get("plugins", []):
        versions.append(
            ("marketplace.json (plugins[].version)", plugin.get("version", ""), marketplace_path)
        )

    for skill_path in find_skill_files():
        match = SKILL_VERSION_RE.search(skill_path.read_text())
        versions.append(
            (
                f"skills/{skill_path.parent.name}/SKILL.md",
                match.group(2) if match else "",
                skill_path,
            )
        )

    return versions


def check_consistency() -> Tuple[bool, List[str]]:
    versions = get_all_versions()
    if not versions:
        return False, ["No version-managed files found"]

    canonical = versions[0][1]
    mismatches: list[str] = []
    for name, version, _ in versions:
        if version != canonical:
            mismatches.append(f"  {name}: {version or '<missing>'} (expected {canonical})")

    if mismatches:
        return False, [f"Version mismatch (canonical: {canonical}):"] + mismatches
    return True, [f"All {len(versions)} version references are {canonical}"]


def set_version(new_version: str) -> int:
    changed = 0

    plugin_path = VERSION_FILES["plugin.json"]
    plugin_data = read_json(plugin_path)
    if plugin_data.get("version") != new_version:
        plugin_data["version"] = new_version
        write_json(plugin_path, plugin_data)
        changed += 1

    marketplace_path = VERSION_FILES["marketplace.json"]
    marketplace_data = read_json(marketplace_path)
    marketplace_changed = False

    metadata = marketplace_data.setdefault("metadata", {})
    if metadata.get("version") != new_version:
        metadata["version"] = new_version
        marketplace_changed = True
        changed += 1

    for plugin in marketplace_data.get("plugins", []):
        if plugin.get("version") != new_version:
            plugin["version"] = new_version
            marketplace_changed = True
            changed += 1

    if marketplace_changed:
        write_json(marketplace_path, marketplace_data)

    for skill_path in find_skill_files():
        text = skill_path.read_text()
        updated = SKILL_VERSION_RE.sub(rf'\g<1>{new_version}\3', text)
        if updated != text:
            skill_path.write_text(updated)
            changed += 1

    return changed


def main(argv: List[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])

    if "--check" in args:
        ok, messages = check_consistency()
        for message in messages:
            print(message)
        return 0 if ok else 1

    if "--current" in args:
        print(read_current_version())
        return 0

    explicit_version = next((arg for arg in args if not arg.startswith("-")), None)
    target_version = explicit_version or bump_patch(read_current_version())

    try:
        parse_version(target_version)
    except ValueError as exc:
        print(f"invalid version {target_version!r}: {exc}", file=sys.stderr)
        return 1

    changed = set_version(target_version)
    print(f"Set version to {target_version} ({changed} updates)")

    ok, messages = check_consistency()
    for message in messages:
        print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
