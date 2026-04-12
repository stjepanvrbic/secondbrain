#!/usr/bin/env python3
"""
emit_hot_memory.py — T11 reader for the SessionStart hook.

Reads `<vault>/brain/hot-memory.md` via filesystem (not MCP — this runs on
every session start, it must be fast), validates it against the T10 schema,
and emits a JSON object `{"systemMessage": "..."}` that the SessionStart
hook passes to Claude Code.

Design
------
The hook MUST always get a parseable JSON object back, even if something is
wrong with the vault — a broken hook destroys the session experience. So
every failure mode emits a fallback `systemMessage` and exits 0.

Failure modes and their fallbacks:
  - Vault path doesn't exist  → "secondbrain not configured. Run init."
  - hot-memory.md missing      → "hot memory is missing. Run /secondbrain:init
                                  or /secondbrain:doctor."
  - hot-memory.md invalid      → "hot memory is invalid (schema version
                                  mismatch or malformed). Run doctor."
  - Any other OSError          → generic "configuration error" fallback

If `--cwd` is supplied and matches a vault entity (per `vault_lookup_cwd`),
its `## Active Project Context` section is appended to the systemMessage.

Exit code is 0 in every case. Stderr carries a one-line diagnostic so
debug logs show what went wrong.

Usage:
    python3 emit_hot_memory.py --vault <vault_path> [--cwd <cwd>]

Stdlib-only, Python 3.8+.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

# Ensure sibling modules are importable from anywhere the script is invoked.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from hot_memory_schema import validate  # type: ignore[reportMissingImports]
from vault_lookup_cwd import (  # type: ignore[reportMissingImports]
    build_active_project_section,
)


HOT_MEMORY_RELATIVE = "brain/hot-memory.md"


# ---------------------------------------------------------------------------
# Fallback messages — kept terse so they don't bloat the context window if
# they end up persisting for a few turns while the user fixes the issue.
# ---------------------------------------------------------------------------

FALLBACK_NOT_CONFIGURED = (
    "secondbrain not configured. Run /secondbrain:init to set up."
)

FALLBACK_HOT_MEMORY_MISSING = (
    "secondbrain hot memory is missing. Run /secondbrain:init or "
    "/secondbrain:doctor to set up. Vault operations may be limited."
)

FALLBACK_HOT_MEMORY_INVALID = (
    "secondbrain hot memory is invalid (schema version mismatch or malformed). "
    "Run /secondbrain:doctor to diagnose."
)

FALLBACK_GENERIC_ERROR = (
    "secondbrain hot memory could not be loaded. Run /secondbrain:doctor to "
    "diagnose."
)


# ---------------------------------------------------------------------------
# Emission
# ---------------------------------------------------------------------------

def _emit(message: str) -> None:
    """Print a single JSON object to stdout. Never prints extra bytes."""
    sys.stdout.write(json.dumps({"systemMessage": message}))
    sys.stdout.write("\n")


def _log_err(msg: str) -> None:
    sys.stderr.write("emit_hot_memory: " + msg + "\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="emit_hot_memory.py",
        description=(
            "Read brain/hot-memory.md from a vault and emit it as a JSON "
            "systemMessage for the SessionStart hook. Always exits 0."
        ),
    )
    parser.add_argument(
        "--vault",
        required=True,
        help="Absolute path to the vault root.",
    )
    parser.add_argument(
        "--cwd",
        required=False,
        default=None,
        help=(
            "Optional current working directory. If supplied and it matches a "
            "vault entity, an Active Project Context section is appended."
        ),
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    vault_path = Path(args.vault).expanduser()
    if not vault_path.is_dir():
        _log_err("vault path does not exist: " + str(vault_path))
        _emit(FALLBACK_NOT_CONFIGURED)
        return 0

    hot_memory_path = vault_path / HOT_MEMORY_RELATIVE
    if not hot_memory_path.is_file():
        _log_err("hot-memory missing at " + str(hot_memory_path))
        _emit(FALLBACK_HOT_MEMORY_MISSING)
        return 0

    try:
        content = hot_memory_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        _log_err("cannot read hot-memory: " + str(exc))
        _emit(FALLBACK_GENERIC_ERROR)
        return 0

    result = validate(content)
    if not result.ok:
        _log_err(
            "hot-memory failed validation: "
            + "; ".join(result.errors[:3])
        )
        _emit(FALLBACK_HOT_MEMORY_INVALID)
        return 0

    message = content.rstrip() + "\n"

    if args.cwd:
        try:
            cwd = Path(args.cwd).expanduser()
            section = build_active_project_section(vault_path, cwd)
        except Exception as exc:  # noqa: BLE001
            # Defensive: if the cwd match crashes, we still want the hot
            # memory delivered. Log and skip the section.
            _log_err("vault_lookup_cwd failed: " + str(exc))
            section = ""
        if section:
            message = message.rstrip() + "\n" + section.rstrip() + "\n"

    _emit(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
