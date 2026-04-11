#!/usr/bin/env python3
"""
doctor_cli.py — CLI entrypoint for the /secondbrain:doctor two-turn flow.

Usage:
    python3 doctor_cli.py --diagnose --vault <path> [--json]
    python3 doctor_cli.py --treat --vault <path> [--interactive]

This is the ONLY way the doctor skill talks to the check engine. Keeping
the CLI tiny (argparse + dispatch) means the skill body can stay small
and the tested logic lives in `doctor_checks.py`.

Exit codes:
    0  all checks passed (--diagnose) OR all treatments succeeded (--treat)
    1  one or more checks failed (--diagnose) OR one or more treatments failed (--treat)
    2  CLI usage error (missing args, unreadable vault path, etc.)

Read-only contract:
    --diagnose MUST NOT mutate the vault. This is enforced by
    `tests/test_doctor_two_turn.py` via a state-hash diff before/after.

Python 3.8+, stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import List

# Make sibling modules importable regardless of cwd.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from doctor_checks import (  # type: ignore[reportMissingImports]  # noqa: E402
    CheckResult,
    run_all_checks,
    run_fixable_treatments,
)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

_STATUS_GLYPH = {
    "pass": "[PASS]",
    "fail": "[FAIL]",
    "skip": "[SKIP]",
    "warning": "[WARN]",
}


def _format_human(results: List[CheckResult]) -> str:
    """Build the pretty diagnostic table as a plain string."""
    lines: List[str] = []
    lines.append("secondbrain doctor report:")
    lines.append("")
    for r in results:
        glyph = _STATUS_GLYPH.get(r.status, "[?]")
        lines.append(f"  {glyph} {r.name}: {r.message}")
        if r.fixable and r.fix_function:
            lines.append(f"         -> doctor can fix this (runs {r.fix_function})")
    lines.append("")

    passed = sum(1 for r in results if r.status == "pass")
    failed = sum(1 for r in results if r.status == "fail")
    warned = sum(1 for r in results if r.status == "warning")
    skipped = sum(1 for r in results if r.status == "skip")
    fixable = sum(1 for r in results if r.status == "fail" and r.fixable)
    lines.append(
        f"  Result: {passed} passed, {failed} failed, "
        f"{warned} warning, {skipped} skipped."
    )

    if failed == 0 and warned == 0:
        lines.append("")
        lines.append("  Your secondbrain is healthy.")
    else:
        lines.append("")
        if fixable > 0:
            lines.append(f"  I can fix {fixable} of these — want me to? (yes/no)")
        else:
            lines.append(
                "  None of the failures are auto-fixable. See per-check messages for manual steps."
            )
    return "\n".join(lines)


def _serialize_results(results: List[CheckResult]) -> dict:
    """Build the JSON payload for `--json` mode."""
    passed = sum(1 for r in results if r.status == "pass")
    failed = sum(1 for r in results if r.status == "fail")
    warned = sum(1 for r in results if r.status == "warning")
    skipped = sum(1 for r in results if r.status == "skip")
    fixable = sum(1 for r in results if r.status == "fail" and r.fixable)
    return {
        "results": [asdict(r) for r in results],
        "summary": {
            "passed": passed,
            "failed": failed,
            "warning": warned,
            "skipped": skipped,
            "fixable_count": fixable,
        },
    }


# ---------------------------------------------------------------------------
# Mode dispatch
# ---------------------------------------------------------------------------

def _diagnose(args: argparse.Namespace) -> int:
    vault_path = Path(args.vault).expanduser().resolve()
    # Note: we run checks even if vault_path doesn't exist — the individual
    # check_vault_reachable handles that case and reports a clean "fail".
    # Failing fast here would hide the other env-var problems doctor is
    # uniquely good at surfacing.
    repo_root = _detect_repo_root()
    results = run_all_checks(vault_path=vault_path, repo_root=repo_root)

    if args.json:
        sys.stdout.write(json.dumps(_serialize_results(results), indent=2))
        sys.stdout.write("\n")
    else:
        sys.stdout.write(_format_human(results))
        sys.stdout.write("\n")
    sys.stdout.flush()

    failed = any(r.status == "fail" for r in results)
    return 1 if failed else 0


def _treat(args: argparse.Namespace) -> int:
    vault_path = Path(args.vault).expanduser().resolve()
    repo_root = _detect_repo_root()

    # Run Phase 1 first to know what needs fixing.
    results = run_all_checks(vault_path=vault_path, repo_root=repo_root)
    # Dispatch fixes.
    step_results = run_fixable_treatments(
        results, vault_path, interactive=args.interactive,
    )

    # Report what happened.
    if not step_results:
        sys.stdout.write(
            "No fixable failures — nothing to treat. Re-run --diagnose "
            "to see current state.\n"
        )
        return 0

    sys.stdout.write("Treatment results:\n\n")
    all_ok = True
    for step_result in step_results:
        marker = "ok" if step_result.success else "FAIL"
        did = " (changed)" if step_result.did_work else " (no change)"
        sys.stdout.write(f"  [{marker}]{did} {step_result.message}\n")
        if not step_result.success:
            all_ok = False
            if step_result.error:
                sys.stdout.write(f"          error: {step_result.error}\n")

    # Re-diagnose and report the new state.
    sys.stdout.write("\nRe-running diagnostic:\n\n")
    results2 = run_all_checks(vault_path=vault_path, repo_root=repo_root)
    sys.stdout.write(_format_human(results2))
    sys.stdout.write("\n")
    sys.stdout.flush()

    return 0 if all_ok else 1


def _detect_repo_root() -> Path | None:
    """Walk up from the CLI script path to find the secondbrain repo root.

    Used to pass a `repo_root` into run_all_checks for the
    `check_core_hooks_path` check. Returns None if we can't find it —
    then the hooks-path check is silently skipped.
    """
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        if (parent / ".git").exists() and (parent / "secondbrain").is_dir():
            return parent
    return None


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "secondbrain doctor — diagnose and optionally treat plugin health issues. "
            "Phase 1 (--diagnose) is strictly read-only; Phase 2 (--treat) only runs "
            "on explicit user confirmation."
        ),
    )
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--diagnose",
        action="store_true",
        help="Run all checks and print a report. Read-only.",
    )
    mode.add_argument(
        "--treat",
        action="store_true",
        help="Run all checks, then invoke fix functions for fixable failures.",
    )
    p.add_argument(
        "--vault",
        required=True,
        help="Path to the Obsidian vault to check.",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="With --diagnose, emit a JSON report instead of the human table.",
    )
    p.add_argument(
        "--interactive",
        action="store_true",
        help="With --treat, allow interactive prompts (e.g. setup_profile).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if not args.vault:
        # argparse should already catch this (required=True), but defense in depth
        print("error: --vault is required", file=sys.stderr)
        return 2

    if args.diagnose:
        return _diagnose(args)
    if args.treat:
        return _treat(args)
    # Shouldn't reach this — mutually_exclusive_group(required=True).
    return 2


if __name__ == "__main__":
    sys.exit(main())
