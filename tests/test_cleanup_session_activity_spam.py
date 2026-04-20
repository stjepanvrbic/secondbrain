"""Tests for cleanup_session_activity_spam.py.

Covers the idempotent-strip behavior, the --threshold gate, and --dry-run.
No git dependencies — stdlib-only.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "secondbrain" / "scripts" / "cleanup_session_activity_spam.py"

sys.path.insert(0, str(REPO_ROOT / "secondbrain" / "scripts"))

from cleanup_session_activity_spam import clean  # type: ignore[reportMissingImports]


SPAM_LINE = "## [2026-04-18 14:32] session-activity | checkpoint\n"
REAL_ENTRY = (
    "## [2026-04-18 09:15] dream-protocol | Run #31\n"
    "\n"
    "- vault clean\n"
    "- hot-memory regenerated\n"
)


def _run_cli(vault: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--vault", str(vault), *extra],
        capture_output=True,
        text=True,
        timeout=20,
    )


class TestCleanPure:
    def test_strips_all_spam(self):
        content = f"# Log\n\n{SPAM_LINE * 5}{REAL_ENTRY}{SPAM_LINE * 3}"
        cleaned, count = clean(content)
        assert count == 8
        assert "session-activity" not in cleaned
        assert "dream-protocol | Run #31" in cleaned

    def test_idempotent(self):
        content = f"# Log\n\n{SPAM_LINE * 5}{REAL_ENTRY}"
        once, first_count = clean(content)
        twice, second_count = clean(once)
        assert first_count == 5
        assert second_count == 0
        assert once == twice

    def test_no_matches_returns_unchanged(self):
        content = f"# Log\n\n{REAL_ENTRY}"
        cleaned, count = clean(content)
        assert count == 0
        assert cleaned == content

    def test_preserves_surrounding_structure(self):
        content = f"# Log\n\n{REAL_ENTRY}\n{SPAM_LINE}{REAL_ENTRY}"
        cleaned, count = clean(content)
        assert count == 1
        # Both real entries survive, in order.
        assert cleaned.index("Run #31") < cleaned.rindex("Run #31")

    def test_collapses_runaway_blank_lines(self):
        # Back-to-back spam used to produce "\n\n\n\n" after naive strip.
        content = f"# Log\n\n{SPAM_LINE}\n{SPAM_LINE}\n{SPAM_LINE}{REAL_ENTRY}"
        cleaned, _ = clean(content)
        assert "\n\n\n" not in cleaned


class TestCLI:
    def test_cli_writes_clean_log(self, tmp_path: Path):
        log = tmp_path / "log.md"
        log.write_text(f"# Log\n\n{SPAM_LINE * 3}{REAL_ENTRY}")

        result = _run_cli(tmp_path)
        assert result.returncode == 0, result.stderr
        assert "removed 3 entries" in result.stdout
        assert "session-activity" not in log.read_text()

    def test_cli_dry_run_does_not_write(self, tmp_path: Path):
        log = tmp_path / "log.md"
        original = f"# Log\n\n{SPAM_LINE * 3}{REAL_ENTRY}"
        log.write_text(original)

        result = _run_cli(tmp_path, "--dry-run")
        assert result.returncode == 0
        assert "would remove 3 entries" in result.stdout
        assert log.read_text() == original

    def test_cli_threshold_skips_small_bloat(self, tmp_path: Path):
        log = tmp_path / "log.md"
        original = f"# Log\n\n{SPAM_LINE * 5}{REAL_ENTRY}"
        log.write_text(original)

        result = _run_cli(tmp_path, "--threshold", "1000")
        assert result.returncode == 0
        assert "leaving log untouched" in result.stdout
        assert log.read_text() == original

    def test_cli_threshold_cleans_when_over(self, tmp_path: Path):
        log = tmp_path / "log.md"
        log.write_text(f"# Log\n\n{SPAM_LINE * 20}{REAL_ENTRY}")

        result = _run_cli(tmp_path, "--threshold", "10")
        assert result.returncode == 0
        assert "removed 20 entries" in result.stdout
        assert "session-activity" not in log.read_text()

    def test_cli_missing_log_is_noop(self, tmp_path: Path):
        result = _run_cli(tmp_path)
        assert result.returncode == 0
        assert "nothing to do" in result.stdout

    def test_cli_missing_vault_errors(self, tmp_path: Path):
        fake = tmp_path / "does-not-exist"
        result = _run_cli(fake)
        assert result.returncode == 1
        assert "vault not found" in result.stderr
