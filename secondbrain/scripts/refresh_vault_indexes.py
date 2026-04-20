#!/usr/bin/env python3
"""
refresh_vault_indexes.py — regenerate hot-memory + manifest in one go.

Purpose: decouple "the user's indexes are fresh" from dream-protocol. Before
v3.6 the only code path that regenerated `brain/hot-memory.md` from vault
sources or rebuilt `_MANIFEST.md` was dream-protocol, which runs once nightly
and can fail silently. When dream fails, the user sees empty hot-memory
sections and a stale manifest until they notice and re-run it.

This script is a thin orchestrator: it shells out to `update_hot_memory.py
--regenerate` and then to `rebuild_manifest.py`, both of which already exist
and do the real work. Invoked by the SessionStart hook (in background) when
`brain/hot-memory.md` is more than ~12 hours old, and by any user or
scheduled task that wants to force a refresh.

Usage:
    python3 refresh_vault_indexes.py --vault ~/cowork
    python3 refresh_vault_indexes.py --vault ~/cowork --skip-manifest
    python3 refresh_vault_indexes.py --vault ~/cowork --skip-hot-memory

Exit codes:
  0 — both steps succeeded (or were skipped by flag)
  1 — vault missing, or at least one step failed

Errors are logged to `<vault>/.secondbrain/ingest-log.md` so doctor's
`check_ingest_log_recent_failures` can surface them to the user.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent


def _log(vault: Path, line: str) -> None:
    """Append a timestamped line to the shared ingest log.

    Same file the ingester writes to, so operators see a single timeline
    of automated vault writes instead of hunting across several logs.
    """
    try:
        log_path = vault / ".secondbrain" / "ingest-log.md"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"[{ts}] refresh_vault_indexes | {line}\n")
    except OSError:
        pass


def _run_step(label: str, cmd: list[str], vault: Path) -> int:
    """Run a sub-step, log stdout+stderr to the ingest log, return the rc.

    Non-zero rcs are logged but do not abort the outer orchestrator — we
    want the manifest rebuild to run even if hot-memory regeneration
    fails, so the user sees as much freshness as we can give them.
    """
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    combined = (result.stdout or "") + (result.stderr or "")
    first_line = combined.strip().splitlines()[0] if combined.strip() else "(no output)"
    if result.returncode == 0:
        _log(vault, f"{label} ok — {first_line}")
    else:
        # Prefix multi-line failure output so each line is attributable in
        # the log. Truncate at 4k to keep the file scannable.
        trimmed = combined[:4000]
        _log(vault, f"{label} FAILED (rc={result.returncode}): {trimmed!r}")
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--vault",
        required=True,
        type=Path,
        help="Path to the vault root.",
    )
    parser.add_argument(
        "--desktop-config-path",
        type=Path,
        default=None,
        help=(
            "Path to Claude Desktop's config (passed through to "
            "update_hot_memory.py). If omitted, defaults to "
            "$SECONDBRAIN_CLAUDE_DESKTOP_CONFIG or the platform default."
        ),
    )
    parser.add_argument(
        "--skip-hot-memory",
        action="store_true",
        help="Skip the hot-memory regenerate step.",
    )
    parser.add_argument(
        "--skip-manifest",
        action="store_true",
        help="Skip the manifest rebuild step.",
    )
    args = parser.parse_args(argv)

    vault: Path = args.vault.expanduser().resolve()
    if not vault.is_dir():
        print(f"error: vault not found: {vault}", file=sys.stderr)
        return 1

    rc = 0

    if not args.skip_hot_memory:
        update_script = _SCRIPT_DIR / "update_hot_memory.py"
        cmd = [
            sys.executable,
            str(update_script),
            "--regenerate",
            "--vault",
            str(vault),
        ]
        desktop_config = args.desktop_config_path or os.environ.get(
            "SECONDBRAIN_CLAUDE_DESKTOP_CONFIG"
        )
        if desktop_config:
            cmd.extend(["--desktop-config-path", str(desktop_config)])
        step_rc = _run_step("hot-memory regenerate", cmd, vault)
        if step_rc != 0:
            rc = step_rc

    if not args.skip_manifest:
        rebuild_script = _SCRIPT_DIR / "rebuild_manifest.py"
        cmd = [sys.executable, str(rebuild_script), str(vault)]
        step_rc = _run_step("manifest rebuild", cmd, vault)
        if step_rc != 0:
            rc = step_rc

    return rc


if __name__ == "__main__":
    sys.exit(main())
