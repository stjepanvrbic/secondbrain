#!/usr/bin/env python3
"""
validate_hot_memory.py — CLI wrapper around `hot_memory_schema.validate()`.

Usage:
    python3 validate_hot_memory.py <path> [--quiet] [--json]

Reads the hot-memory markdown file at `<path>`, runs the schema validator,
and prints a structured report. Used by:
  - `doctor` (check_hot_memory_schema) to verify the file still parses.
  - `update_hot_memory.py` (internally — but that script imports the
    module directly rather than shelling out).
  - CI sanity checks on sample hot-memory files.

Exit codes:
    0  validation passed (warnings allowed)
    1  validation failed (missing file, schema error, token budget overrun, ...)

No side effects beyond reading the file and writing to stdout/stderr.
Stdlib-only, Python 3.8+.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path
from typing import List, Optional

# Ensure sibling module import works when run from anywhere.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from hot_memory_schema import (  # type: ignore[reportMissingImports]
    ValidationResult,
    validate,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="validate_hot_memory.py",
        description=(
            "Validate a hot-memory markdown file against the canonical schema. "
            "Exits 0 on success (warnings allowed), 1 on any error."
        ),
    )
    parser.add_argument(
        "path",
        help="Path to the hot-memory.md file to validate.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress all stdout. Exit code still indicates status.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit the ValidationResult as JSON on stdout.",
    )
    return parser


def _result_to_dict(result: ValidationResult) -> dict:
    # dataclasses.asdict handles the plain fields. ValidationResult has no
    # nested dataclasses so this is enough.
    return dataclasses.asdict(result)


def _print_human(result: ValidationResult, path: Path) -> None:
    """Render a small one-screen report to stdout."""
    status = "OK" if result.ok else "FAIL"
    print(f"[{status}] {path}")
    print(
        "  schema_version={}  tokens={}  sections={}".format(
            result.schema_version,
            result.token_estimate,
            len(result.sections_found),
        )
    )
    if result.missing_sections:
        print("  missing sections: " + ", ".join(result.missing_sections))
    if result.extra_sections:
        print("  extra sections: " + ", ".join(result.extra_sections))
    if result.errors:
        print("  errors:")
        for err in result.errors:
            print("    - " + err)
    if result.warnings:
        print("  warnings:")
        for warning in result.warnings:
            print("    - " + warning)


def _emit_not_found_json(path: Path) -> dict:
    """Build a ValidationResult-shaped dict describing a missing file."""
    return {
        "ok": False,
        "schema_version": None,
        "token_estimate": 0,
        "sections_found": [],
        "missing_sections": [],
        "extra_sections": [],
        "errors": [f"file not found: {path}"],
        "warnings": [],
    }


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    path = Path(args.path)
    if not path.exists():
        if args.json_output:
            if not args.quiet:
                print(json.dumps(_emit_not_found_json(path)))
            sys.stderr.write(f"error: file not found: {path}\n")
            return 1
        if not args.quiet:
            sys.stderr.write(f"error: file not found: {path}\n")
        return 1

    if not path.is_file():
        if not args.quiet:
            sys.stderr.write(f"error: not a file: {path}\n")
        return 1

    try:
        content = path.read_text()
    except OSError as exc:
        if not args.quiet:
            sys.stderr.write(f"error: cannot read {path}: {exc}\n")
        return 1

    result = validate(content)

    if args.json_output:
        if not args.quiet:
            print(json.dumps(_result_to_dict(result)))
    else:
        if not args.quiet:
            _print_human(result, path)

    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
