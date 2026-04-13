"""Tests for doctor_report.py merge and rendering helpers."""

from __future__ import annotations

import json
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "secondbrain" / "scripts"))

from doctor_checks import CheckResult  # type: ignore[reportMissingImports]
from doctor_report import (  # type: ignore[reportMissingImports]
    format_human,
    main,
    merge_results,
    serialize_results,
)


class TestMergeResults:
    def test_supplemental_results_override_raw_by_check_name(self):
        raw_results = [
            CheckResult("obsidian_api_key", "warning", "session validation required", False),
            CheckResult("mcp_connection", "warning", "session validation required", False),
            CheckResult("manifest", "pass", "manifest exists", False),
        ]
        supplemental_results = [
            CheckResult("obsidian_api_key", "pass", "resolved from session evidence", False),
            CheckResult("mcp_connection", "fail", "session probe could not connect", False),
        ]

        merged = merge_results(raw_results, supplemental_results)

        assert [result.name for result in merged] == [
            "obsidian_api_key",
            "mcp_connection",
            "manifest",
        ]
        assert merged[0].status == "pass"
        assert merged[1].status == "fail"
        assert merged[2].status == "pass"

    def test_summary_is_recomputed_from_merged_results(self):
        raw_results = [
            CheckResult("obsidian_running", "warning", "session validation required", False),
            CheckResult("log_md", "fail", "missing log.md", True, "create_log_md"),
        ]
        supplemental_results = [
            CheckResult("obsidian_running", "pass", "MCP session probe succeeded", False),
        ]

        merged = merge_results(raw_results, supplemental_results)
        payload = serialize_results(merged)

        assert payload["summary"] == {
            "passed": 1,
            "failed": 1,
            "warning": 0,
            "skipped": 0,
            "fixable_count": 1,
        }


class TestFormatHuman:
    def test_human_report_uses_recomputed_summary(self):
        results = [
            CheckResult("obsidian_running", "pass", "session probe succeeded", False),
            CheckResult("log_md", "fail", "missing log.md", True, "create_log_md"),
        ]

        report = format_human(results)

        assert "Result: 1 passed, 1 failed, 0 warning, 0 skipped." in report
        assert "I can fix 1 of these" in report


class TestDoctorReportCli:
    def test_cli_merges_raw_and_supplemental_json(self, tmp_path: Path, capsys):
        raw_json = tmp_path / "raw.json"
        supplemental_json = tmp_path / "supplemental.json"

        raw_json.write_text(json.dumps({
            "results": [
                {
                    "name": "obsidian_running",
                    "status": "warning",
                    "message": "session validation required",
                    "fixable": False,
                    "fix_function": None,
                    "details": {},
                },
            ],
            "summary": {"passed": 0, "failed": 0, "warning": 1, "skipped": 0, "fixable_count": 0},
        }))
        supplemental_json.write_text(json.dumps({
            "results": [
                {
                    "name": "obsidian_running",
                    "status": "pass",
                    "message": "session probe succeeded",
                    "fixable": False,
                    "fix_function": None,
                    "details": {"evidence_scope": "session"},
                },
            ],
            "summary": {"passed": 1, "failed": 0, "warning": 0, "skipped": 0, "fixable_count": 0},
        }))

        exit_code = main([
            "--raw-json",
            str(raw_json),
            "--supplemental-json",
            str(supplemental_json),
            "--json",
        ])

        payload = json.loads(capsys.readouterr().out)
        assert exit_code == 0
        assert payload["results"][0]["status"] == "pass"
        assert payload["summary"]["passed"] == 1
        assert payload["summary"]["warning"] == 0
