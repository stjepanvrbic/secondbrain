#!/usr/bin/env python3
"""
doctor_report.py — shared rendering and merge helpers for doctor results.

This module exists for the session-layer doctor flow:

  1. `doctor_cli.py --diagnose --json` produces raw subprocess evidence
  2. the skill can gather stronger session-only evidence
  3. this helper merges both sets and renders the final report

The merge contract is intentionally simple: supplemental results are assumed
to be stronger evidence for matching check names and replace raw results.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List

# Make sibling modules importable regardless of cwd.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from doctor_checks import CheckResult  # type: ignore[reportMissingImports]  # noqa: E402


_STATUS_GLYPH = {
    "pass": "[PASS]",
    "fail": "[FAIL]",
    "skip": "[SKIP]",
    "warning": "[WARN]",
}


def format_human(results: List[CheckResult]) -> str:
    lines: List[str] = []
    lines.append("secondbrain doctor report:")
    lines.append("")
    for result in results:
        glyph = _STATUS_GLYPH.get(result.status, "[?]")
        lines.append(f"  {glyph} {result.name}: {result.message}")
        if result.fixable and result.fix_function:
            lines.append(f"         -> doctor can fix this (runs {result.fix_function})")
    lines.append("")

    summary = summarize_results(results)
    lines.append(
        "  Result: "
        f"{summary['passed']} passed, "
        f"{summary['failed']} failed, "
        f"{summary['warning']} warning, "
        f"{summary['skipped']} skipped."
    )

    if summary["failed"] == 0 and summary["warning"] == 0:
        lines.append("")
        lines.append("  Your secondbrain is healthy.")
    else:
        lines.append("")
        if summary["fixable_count"] > 0:
            lines.append(f"  I can fix {summary['fixable_count']} of these — want me to? (yes/no)")
        else:
            lines.append(
                "  None of the failures are auto-fixable. See per-check messages for manual steps."
            )
    return "\n".join(lines)


def summarize_results(results: Iterable[CheckResult]) -> Dict[str, int]:
    result_list = list(results)
    passed = sum(1 for result in result_list if result.status == "pass")
    failed = sum(1 for result in result_list if result.status == "fail")
    warned = sum(1 for result in result_list if result.status == "warning")
    skipped = sum(1 for result in result_list if result.status == "skip")
    fixable = sum(1 for result in result_list if result.status == "fail" and result.fixable)
    return {
        "passed": passed,
        "failed": failed,
        "warning": warned,
        "skipped": skipped,
        "fixable_count": fixable,
    }


def serialize_results(results: List[CheckResult]) -> Dict[str, Any]:
    return {
        "results": [asdict(result) for result in results],
        "summary": summarize_results(results),
    }


def merge_results(raw_results: List[CheckResult], supplemental_results: List[CheckResult]) -> List[CheckResult]:
    merged_by_name = {result.name: result for result in raw_results}
    order = [result.name for result in raw_results]

    for result in supplemental_results:
        if result.name not in merged_by_name:
            order.append(result.name)
        merged_by_name[result.name] = result

    return [merged_by_name[name] for name in order]


def parse_results_payload(payload: Dict[str, Any]) -> List[CheckResult]:
    raw_results = payload.get("results")
    if not isinstance(raw_results, list):
        raise ValueError("doctor results payload must contain a top-level 'results' list")

    results: List[CheckResult] = []
    for item in raw_results:
        if not isinstance(item, dict):
            raise ValueError("each doctor result must be an object")
        results.append(
            CheckResult(
                name=str(item["name"]),
                status=str(item["status"]),
                message=str(item["message"]),
                fixable=bool(item["fixable"]),
                fix_function=item.get("fix_function"),
                details=item.get("details") or {},
            )
        )
    return results


def load_results_file(path: Path) -> List[CheckResult]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError("doctor results JSON must be an object")
    return parse_results_payload(payload)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Merge raw and supplemental doctor JSON and render the final report.",
    )
    parser.add_argument("--raw-json", required=True, help="Path to raw doctor_cli --diagnose --json output.")
    parser.add_argument(
        "--supplemental-json",
        help="Optional path to session-layer supplemental results in the same JSON shape.",
    )
    parser.add_argument("--json", action="store_true", help="Emit merged JSON instead of the human report.")
    return parser


def main(argv: List[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    raw_results = load_results_file(Path(args.raw_json).expanduser().resolve())
    results = raw_results
    if args.supplemental_json:
        supplemental_results = load_results_file(Path(args.supplemental_json).expanduser().resolve())
        results = merge_results(raw_results, supplemental_results)

    if args.json:
        sys.stdout.write(json.dumps(serialize_results(results), indent=2))
        sys.stdout.write("\n")
    else:
        sys.stdout.write(format_human(results))
        sys.stdout.write("\n")

    return 1 if any(result.status == "fail" for result in results) else 0


if __name__ == "__main__":
    sys.exit(main())
