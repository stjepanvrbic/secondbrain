"""Tests for rotate_log.py."""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "secondbrain" / "scripts" / "rotate_log.py"

sys.path.insert(0, str(REPO_ROOT / "secondbrain" / "scripts"))

from rotate_log import rotate  # type: ignore[reportMissingImports]


def _entry(date_str: str, op: str = "session-end", body: str = "note") -> str:
    return f"## [{date_str} 09:00] {op} | summary\n{body}\n\n"


def _write_log(vault: Path, *entries: str, preamble: str = "# Log\n\n") -> Path:
    log = vault / "log.md"
    log.write_text(preamble + "".join(entries))
    return log


def _run_cli(vault: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--vault", str(vault), *extra],
        capture_output=True,
        text=True,
        timeout=20,
    )


class TestRotatePure:
    def test_moves_old_entries_keeps_recent(self, tmp_path: Path):
        today = datetime.now().date()
        old_date = (today - timedelta(days=60)).isoformat()
        recent_date = (today - timedelta(days=5)).isoformat()

        _write_log(tmp_path, _entry(old_date), _entry(recent_date))

        moved, kept = rotate(tmp_path, max_age_days=30, max_size_mb=None, dry_run=False)
        assert moved == 1
        assert kept == 1

        remaining = (tmp_path / "log.md").read_text()
        assert old_date not in remaining
        assert recent_date in remaining

        archive_files = list((tmp_path / "archive").glob("log-*.md"))
        assert len(archive_files) == 1
        archive_content = archive_files[0].read_text()
        assert old_date in archive_content

    def test_idempotent(self, tmp_path: Path):
        today = datetime.now().date()
        old_date = (today - timedelta(days=60)).isoformat()
        _write_log(tmp_path, _entry(old_date))

        moved1, _ = rotate(tmp_path, max_age_days=30, max_size_mb=None, dry_run=False)
        moved2, _ = rotate(tmp_path, max_age_days=30, max_size_mb=None, dry_run=False)
        assert moved1 == 1
        assert moved2 == 0

    def test_preserves_preamble(self, tmp_path: Path):
        today = datetime.now().date()
        old_date = (today - timedelta(days=60)).isoformat()
        _write_log(tmp_path, _entry(old_date), preamble="# Log\n\n> custom preamble\n\n")

        rotate(tmp_path, max_age_days=30, max_size_mb=None, dry_run=False)

        remaining = (tmp_path / "log.md").read_text()
        assert "custom preamble" in remaining

    def test_preserves_unparseable_dates(self, tmp_path: Path):
        log = tmp_path / "log.md"
        log.write_text(
            "# Log\n\n"
            "## [NOT-A-DATE] session | weird\nstuff\n\n"
            "## [2020-01-01 00:00] ancient | old\nbody\n\n"
        )
        moved, kept = rotate(tmp_path, max_age_days=30, max_size_mb=None, dry_run=False)
        # The unparseable entry is kept (conservative default);
        # the ancient one is moved.
        assert moved == 1
        assert kept == 1
        remaining = log.read_text()
        assert "NOT-A-DATE" in remaining

    def test_size_gate_skips_small_logs(self, tmp_path: Path):
        today = datetime.now().date()
        old_date = (today - timedelta(days=60)).isoformat()
        _write_log(tmp_path, _entry(old_date))

        # Tiny log + 10MB threshold → skip.
        moved, _ = rotate(tmp_path, max_age_days=30, max_size_mb=10, dry_run=False)
        assert moved == 0

    def test_dry_run_does_not_write(self, tmp_path: Path):
        today = datetime.now().date()
        old_date = (today - timedelta(days=60)).isoformat()
        log = _write_log(tmp_path, _entry(old_date))
        before = log.read_text()

        moved, _ = rotate(tmp_path, max_age_days=30, max_size_mb=None, dry_run=True)
        assert moved == 1
        assert log.read_text() == before
        assert not (tmp_path / "archive").exists()


class TestCLI:
    def test_cli_reports_counts(self, tmp_path: Path):
        today = datetime.now().date()
        old_date = (today - timedelta(days=60)).isoformat()
        recent_date = (today - timedelta(days=5)).isoformat()
        _write_log(tmp_path, _entry(old_date), _entry(recent_date))

        result = _run_cli(tmp_path, "--max-age-days", "30")
        assert result.returncode == 0, result.stderr
        assert "moved 1 entries" in result.stdout
        assert "kept 1 recent" in result.stdout

    def test_cli_missing_vault_errors(self, tmp_path: Path):
        result = _run_cli(tmp_path / "nope")
        assert result.returncode == 1
        assert "vault not found" in result.stderr
