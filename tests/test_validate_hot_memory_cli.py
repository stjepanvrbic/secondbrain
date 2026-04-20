"""Tests for validate_hot_memory.py — CLI wrapper around hot_memory_schema.validate().

Runs the script as a real subprocess so stdout/stderr/exit-code contracts
are exercised end-to-end.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
VALIDATE_HOT_MEMORY_SCRIPT = (
    REPO_ROOT / "secondbrain" / "scripts" / "validate_hot_memory.py"
)

sys.path.insert(0, str(REPO_ROOT / "secondbrain" / "scripts"))

from hot_memory_schema import INITIAL_TEMPLATE  # type: ignore[reportMissingImports]


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATE_HOT_MEMORY_SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def valid_hot_memory(tmp_path: Path) -> Path:
    path = tmp_path / "hot-memory.md"
    path.write_text(INITIAL_TEMPLATE)
    return path


@pytest.fixture
def invalid_hot_memory(tmp_path: Path) -> Path:
    path = tmp_path / "hot-memory.md"
    # Missing required sections + no frontmatter.
    path.write_text("# Not actually a hot-memory file\n\nJust some text.\n")
    return path


# ---------------------------------------------------------------------------
# Script presence + basic invocation
# ---------------------------------------------------------------------------

class TestScriptPresence:
    def test_script_exists(self):
        assert VALIDATE_HOT_MEMORY_SCRIPT.is_file()

    def test_script_help(self):
        r = _run_cli("--help")
        assert r.returncode == 0
        assert "validate" in r.stdout.lower() or "hot" in r.stdout.lower()


# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------

class TestExitCodes:
    def test_valid_file_exits_zero(self, valid_hot_memory: Path):
        r = _run_cli(str(valid_hot_memory))
        assert r.returncode == 0, r.stderr

    def test_invalid_file_exits_one(self, invalid_hot_memory: Path):
        r = _run_cli(str(invalid_hot_memory))
        assert r.returncode == 1

    def test_missing_file_exits_one(self, tmp_path: Path):
        missing = tmp_path / "nonexistent.md"
        r = _run_cli(str(missing))
        assert r.returncode == 1
        # Clear error message on stderr
        assert "not found" in r.stderr.lower() or "does not exist" in r.stderr.lower()


# ---------------------------------------------------------------------------
# Output format
# ---------------------------------------------------------------------------

class TestOutput:
    def test_default_output_is_human_readable(self, valid_hot_memory: Path):
        r = _run_cli(str(valid_hot_memory))
        assert r.returncode == 0
        assert r.stdout.strip()
        # Should mention validation status or token count.
        lower = r.stdout.lower()
        assert "ok" in lower or "valid" in lower or "token" in lower

    def test_default_output_on_invalid_shows_errors(self, invalid_hot_memory: Path):
        r = _run_cli(str(invalid_hot_memory))
        assert r.returncode == 1
        # The error list must be rendered somewhere (stdout or stderr).
        combined = (r.stdout + r.stderr).lower()
        assert "frontmatter" in combined or "missing" in combined or "error" in combined

    def test_quiet_suppresses_stdout(self, valid_hot_memory: Path):
        r = _run_cli(str(valid_hot_memory), "--quiet")
        assert r.returncode == 0
        # Quiet mode: no stdout. (stderr may still carry errors, but we
        # expect clean validation → nothing at all.)
        assert r.stdout == "" or r.stdout.strip() == ""

    def test_quiet_still_exits_one_on_invalid(self, invalid_hot_memory: Path):
        r = _run_cli(str(invalid_hot_memory), "--quiet")
        assert r.returncode == 1

    def test_json_output_parses(self, valid_hot_memory: Path):
        r = _run_cli(str(valid_hot_memory), "--json")
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert data["ok"] is True
        assert data["schema_version"] == 1
        assert "token_estimate" in data
        assert "sections_found" in data
        assert "missing_sections" in data
        assert "extra_sections" in data
        assert "errors" in data
        assert "warnings" in data

    def test_json_output_on_invalid(self, invalid_hot_memory: Path):
        r = _run_cli(str(invalid_hot_memory), "--json")
        assert r.returncode == 1
        data = json.loads(r.stdout)
        assert data["ok"] is False
        assert data["errors"]

    def test_json_on_missing_file_still_emits_json(self, tmp_path: Path):
        """Missing-file reports should be machine-parseable if --json was
        requested, so callers (doctor, ingester) can parse exit-1 responses."""
        missing = tmp_path / "nonexistent.md"
        r = _run_cli(str(missing), "--json")
        assert r.returncode == 1
        # Either the JSON is on stdout (preferred) or the stderr has a
        # clear message. Accept both.
        if r.stdout.strip():
            data = json.loads(r.stdout)
            assert data["ok"] is False
