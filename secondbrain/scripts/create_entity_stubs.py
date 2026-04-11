#!/usr/bin/env python3
"""
create_entity_stubs.py — Create stub entity files in an Obsidian vault.

Python 3.8+, zero external dependencies.

Usage:
    python3 create_entity_stubs.py /path/to/vault entity-name [entity-name ...]
    python3 create_entity_stubs.py /path/to/vault --from-json verify-output.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import List, Optional


TEMPLATE = """\
---
type: unknown
domains: []
created: {date}
updated: {date}
---
# {display_name}

> Stub — expand this entity as information becomes available.
"""


def sanitize_name(name: str) -> str:
    """Strip trailing backslashes and invalid filename characters."""
    return name.strip().rstrip("\\").replace("\\", "").strip()


def kebab_to_display(name: str) -> str:
    return " ".join(word.capitalize() for word in name.split("-"))


def extract_names_from_json(path: Path) -> List[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    names: List[str] = []
    for check in data.get("checks", []):
        if check.get("name") != "entity-stubs":
            continue
        for issue in check.get("issues", []):
            file_val = issue.get("file", "")
            if file_val.startswith("entities/") and file_val.endswith(".md"):
                names.append(file_val.removeprefix("entities/").removesuffix(".md"))
    return names


def create_stubs(vault: Path, names: List[str]) -> int:
    entities_dir = vault / "entities"
    entities_dir.mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()
    created = 0

    for raw_name in names:
        name = sanitize_name(raw_name)
        if not name:
            continue
        target = entities_dir / f"{name}.md"
        if target.exists():
            print(f"SKIP {target.relative_to(vault)} (already exists)")
            continue
        target.write_text(TEMPLATE.format(date=today, display_name=kebab_to_display(name)))
        print(f"CREATED {target.relative_to(vault)}")
        created += 1

    print(f"\n{created} file(s) created, {len(names) - created} skipped.")
    return created


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create stub entity files in an Obsidian vault.")
    parser.add_argument("vault", help="Path to the Obsidian vault root")
    parser.add_argument("names", nargs="*", help="Entity names in kebab-case")
    parser.add_argument("--from-json", dest="json_file", help="Read entity names from verify_vault.py JSON output")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    vault = Path(args.vault)
    if not vault.is_dir():
        print(f"Error: vault not found: '{args.vault}'", file=sys.stderr)
        return 1

    names: List[str] = list(args.names)

    if args.json_file:
        json_path = Path(args.json_file)
        if not json_path.is_file():
            print(f"Error: JSON file not found: '{args.json_file}'", file=sys.stderr)
            return 1
        names.extend(extract_names_from_json(json_path))

    if not names:
        print("Error: no entity names provided", file=sys.stderr)
        return 1

    create_stubs(vault, names)
    return 0


if __name__ == "__main__":
    sys.exit(main())
