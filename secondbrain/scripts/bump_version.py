#!/usr/bin/env python3
"""
bump_version.py — Bump version across all files that contain it.

Ensures plugin.json, marketplace.json, and all SKILL.md frontmatter
stay in sync. Bumps patch by default, or accepts explicit version.

Usage:
    python3 bump_version.py                    # auto bump patch
    python3 bump_version.py 3.1.0             # set explicit version
    python3 bump_version.py --check            # verify all versions match (exit 1 if not)
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import List, Optional, Tuple

PLUGIN_ROOT = Path(__file__).resolve().parent.parent  # secondbrain/
REPO_ROOT = PLUGIN_ROOT.parent                        # repo root (contains .claude-plugin/marketplace.json)

VERSION_FILES = {
    "plugin.json": PLUGIN_ROOT / ".claude-plugin" / "plugin.json",
    "marketplace.json": REPO_ROOT / ".claude-plugin" / "marketplace.json",
}

SKILLS_DIR = PLUGIN_ROOT / "skills"
SKILL_VERSION_RE = re.compile(r'^(\s*version:\s*")([^"]+)(")', re.MULTILINE)


def read_current_version() -> str:
    """Read version from plugin.json (the canonical source)."""
    data = json.loads(VERSION_FILES["plugin.json"].read_text())
    return data["version"]


def parse_version(v: str) -> Tuple[int, int, int]:
    parts = v.split(".")
    return int(parts[0]), int(parts[1]), int(parts[2])


def bump_patch(v: str) -> str:
    major, minor, patch = parse_version(v)
    return f"{major}.{minor}.{patch + 1}"


def find_skill_files() -> List[Path]:
    return sorted(SKILLS_DIR.glob("*/SKILL.md"))


def get_all_versions() -> List[Tuple[str, str, Path]]:
    """Return [(source_name, version, path), ...] for all version locations."""
    versions = []

    # plugin.json
    p = VERSION_FILES["plugin.json"]
    data = json.loads(p.read_text())
    versions.append(("plugin.json", data["version"], p))

    # marketplace.json — two version locations that must stay in lockstep
    p = VERSION_FILES["marketplace.json"]
    data = json.loads(p.read_text())
    # 1) top-level metadata.version — catalog-level version Cowork/Code use
    #    to detect marketplace updates. If this doesn't bump, the marketplace
    #    is silently treated as unchanged.
    metadata_version = data.get("metadata", {}).get("version", "")
    if metadata_version:
        versions.append(("marketplace.json (metadata.version)", metadata_version, p))
    # 2) per-plugin version
    for plugin in data.get("plugins", []):
        versions.append(("marketplace.json (plugins[].version)", plugin.get("version", ""), p))

    # SKILL.md files
    for skill_path in find_skill_files():
        text = skill_path.read_text()
        m = SKILL_VERSION_RE.search(text)
        if m:
            versions.append((f"skills/{skill_path.parent.name}/SKILL.md", m.group(2), skill_path))

    return versions


def check_consistency() -> Tuple[bool, List[str]]:
    """Check all versions match. Returns (consistent, messages)."""
    versions = get_all_versions()
    if not versions:
        return False, ["No version files found"]

    canonical = versions[0][1]
    mismatches = []
    for name, version, _ in versions:
        if version != canonical:
            mismatches.append(f"  {name}: {version} (expected {canonical})")

    if mismatches:
        return False, [f"Version mismatch (canonical: {canonical}):"] + mismatches
    return True, [f"All {len(versions)} version references are {canonical}"]


def set_version(new_version: str) -> int:
    """Set version everywhere. Returns count of files changed."""
    changed = 0

    # plugin.json
    p = VERSION_FILES["plugin.json"]
    data = json.loads(p.read_text())
    if data["version"] != new_version:
        data["version"] = new_version
        p.write_text(json.dumps(data, indent=2) + "\n")
        changed += 1

    # marketplace.json — update BOTH metadata.version and plugins[].version.
    # metadata.version is the marketplace catalog version; letting it drift
    # from plugin.version is how we silently broke Cowork update detection.
    p = VERSION_FILES["marketplace.json"]
    data = json.loads(p.read_text())
    metadata = data.setdefault("metadata", {})
    if metadata.get("version") != new_version:
        metadata["version"] = new_version
        changed += 1
    for plugin in data.get("plugins", []):
        if plugin.get("version") != new_version:
            plugin["version"] = new_version
            changed += 1
    p.write_text(json.dumps(data, indent=2) + "\n")

    # SKILL.md files
    for skill_path in find_skill_files():
        text = skill_path.read_text()
        new_text = SKILL_VERSION_RE.sub(rf'\g<1>{new_version}\3', text)
        if new_text != text:
            skill_path.write_text(new_text)
            changed += 1

    return changed


def main(argv: Optional[List[str]] = None) -> int:
    args = argv if argv is not None else sys.argv[1:]

    if "--check" in args:
        ok, messages = check_consistency()
        for msg in messages:
            print(msg)
        return 0 if ok else 1

    current = read_current_version()

    if args and not args[0].startswith("-"):
        new_version = args[0]
    else:
        new_version = bump_patch(current)

    print(f"{current} -> {new_version}")
    changed = set_version(new_version)
    print(f"Updated {changed} files")

    # Verify
    ok, messages = check_consistency()
    for msg in messages:
        print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
