"""Tests for emit_hot_memory.py — T11 reader-side CLI for the SessionStart hook.

The SessionStart hook's shell wrapper (emit-hot-memory.sh) delegates to this
Python script to load `<vault>/brain/hot-memory.md` via filesystem, validate
it, and emit `{"systemMessage": "..."}` for Claude Code to ingest.

Failure modes all emit valid JSON (so the hook always gets something
parseable). Exit code is 0 in all cases — we don't want to break sessions
because of a missing hot-memory file.

Runs the script as a real subprocess to exercise stdout/stderr/exit-code
contracts end-to-end.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EMIT_HOT_MEMORY_SCRIPT = (
    REPO_ROOT / "secondbrain" / "scripts" / "emit_hot_memory.py"
)

sys.path.insert(0, str(REPO_ROOT / "secondbrain" / "scripts"))

from hot_memory_schema import INITIAL_TEMPLATE  # type: ignore[reportMissingImports]


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(EMIT_HOT_MEMORY_SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _make_vault(tmp_path: Path, hot_memory_body: str | None = None) -> Path:
    """Create a minimal vault with optional brain/hot-memory.md."""
    vault = tmp_path / "vault"
    (vault / "brain").mkdir(parents=True)
    (vault / "entities").mkdir(parents=True)
    (vault / "log.md").write_text("# Log\n")
    if hot_memory_body is not None:
        (vault / "brain" / "hot-memory.md").write_text(hot_memory_body)
    return vault


# ---------------------------------------------------------------------------
# Script presence
# ---------------------------------------------------------------------------

class TestScriptPresence:
    def test_script_exists(self):
        assert EMIT_HOT_MEMORY_SCRIPT.is_file()

    def test_help_runs(self):
        r = _run_cli("--help")
        assert r.returncode == 0


# ---------------------------------------------------------------------------
# Happy path: valid hot-memory → JSON systemMessage
# ---------------------------------------------------------------------------

class TestValidHotMemory:
    def test_valid_hot_memory_emits_json_with_content(self, tmp_path: Path):
        vault = _make_vault(tmp_path, INITIAL_TEMPLATE)
        r = _run_cli("--vault", str(vault))
        assert r.returncode == 0, r.stderr
        data = json.loads(r.stdout)
        assert "systemMessage" in data
        assert "Identity & Directive" in data["systemMessage"]
        assert "Vault Layout" in data["systemMessage"]

    def test_valid_hot_memory_system_message_is_string(self, tmp_path: Path):
        vault = _make_vault(tmp_path, INITIAL_TEMPLATE)
        r = _run_cli("--vault", str(vault))
        data = json.loads(r.stdout)
        assert isinstance(data["systemMessage"], str)

    def test_json_always_parseable_valid(self, tmp_path: Path):
        """Even on success, stdout is strictly a single JSON object."""
        vault = _make_vault(tmp_path, INITIAL_TEMPLATE)
        r = _run_cli("--vault", str(vault))
        assert r.returncode == 0
        # json.loads will raise if there's any extra chatter.
        obj = json.loads(r.stdout)
        assert isinstance(obj, dict)


# ---------------------------------------------------------------------------
# Fallback: missing hot-memory
# ---------------------------------------------------------------------------

class TestMissingHotMemory:
    def test_missing_hot_memory_emits_fallback(self, tmp_path: Path):
        vault = _make_vault(tmp_path, None)  # no hot-memory.md
        r = _run_cli("--vault", str(vault))
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert "systemMessage" in data
        msg = data["systemMessage"].lower()
        assert "missing" in msg or "not configured" in msg or "doctor" in msg

    def test_missing_vault_path_emits_fallback(self, tmp_path: Path):
        missing = tmp_path / "no-such-vault"
        r = _run_cli("--vault", str(missing))
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert "systemMessage" in data
        msg = data["systemMessage"].lower()
        assert "not configured" in msg or "init" in msg or "missing" in msg


# ---------------------------------------------------------------------------
# Fallback: invalid hot-memory
# ---------------------------------------------------------------------------

class TestInvalidHotMemory:
    def test_invalid_schema_emits_fallback(self, tmp_path: Path):
        # Missing frontmatter entirely — validator will reject.
        vault = _make_vault(tmp_path, "# Not really hot memory\n\nNope.\n")
        r = _run_cli("--vault", str(vault))
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert "systemMessage" in data
        msg = data["systemMessage"].lower()
        assert "invalid" in msg or "doctor" in msg or "malformed" in msg

    def test_invalid_still_emits_valid_json(self, tmp_path: Path):
        vault = _make_vault(tmp_path, "# garbage")
        r = _run_cli("--vault", str(vault))
        obj = json.loads(r.stdout)
        assert isinstance(obj, dict)
        assert "systemMessage" in obj


# ---------------------------------------------------------------------------
# Active Project Context: cwd matches a vault entity
# ---------------------------------------------------------------------------

class TestActiveProjectContext:
    def test_cwd_match_appends_project_section(self, tmp_path: Path):
        vault = _make_vault(tmp_path, INITIAL_TEMPLATE)
        project = tmp_path / "myproj"
        project.mkdir()
        (vault / "entities" / "myproj.md").write_text(
            "---\ntype: project\npaths:\n  - "
            + str(project)
            + "\n---\n# Myproj\n"
        )
        r = _run_cli("--vault", str(vault), "--cwd", str(project))
        assert r.returncode == 0, r.stderr
        data = json.loads(r.stdout)
        assert "Active Project Context" in data["systemMessage"]
        assert "myproj" in data["systemMessage"]

    def test_cwd_no_match_no_project_section(self, tmp_path: Path):
        vault = _make_vault(tmp_path, INITIAL_TEMPLATE)
        other = tmp_path / "elsewhere"
        other.mkdir()
        # No entity has `paths: [elsewhere]` and no basename matches.
        (vault / "entities" / "unrelated.md").write_text(
            "---\ntype: person\n---\n# Unrelated\n"
        )
        r = _run_cli("--vault", str(vault), "--cwd", str(other))
        assert r.returncode == 0, r.stderr
        data = json.loads(r.stdout)
        assert "Active Project Context" not in data["systemMessage"]

    def test_cwd_omitted_no_project_section(self, tmp_path: Path):
        vault = _make_vault(tmp_path, INITIAL_TEMPLATE)
        r = _run_cli("--vault", str(vault))
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert "Active Project Context" not in data["systemMessage"]


# ---------------------------------------------------------------------------
# Exit code is always 0 (we never break sessions)
# ---------------------------------------------------------------------------

class TestAlwaysExitZero:
    def test_missing_vault_exits_zero(self, tmp_path: Path):
        missing = tmp_path / "nope"
        r = _run_cli("--vault", str(missing))
        assert r.returncode == 0

    def test_empty_file_exits_zero(self, tmp_path: Path):
        vault = _make_vault(tmp_path, "")
        r = _run_cli("--vault", str(vault))
        assert r.returncode == 0

    def test_bad_vault_string_exits_zero(self):
        r = _run_cli("--vault", "/definitely/not/a/path/anywhere/ever")
        assert r.returncode == 0
