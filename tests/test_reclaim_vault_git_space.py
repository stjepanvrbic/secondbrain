"""Tests for reclaim_vault_git_space.py.

Stdlib-only. Uses tmp_path for isolated vaults with synthetic `.git` dirs —
no real git invocation needed.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "secondbrain" / "scripts" / "reclaim_vault_git_space.py"


def _fake_git_dir(vault: Path) -> Path:
    git_dir = vault / ".git"
    git_dir.mkdir(parents=True)
    (git_dir / "config").write_text("[core]\n    repositoryformatversion = 0\n")
    objects = git_dir / "objects"
    objects.mkdir()
    (objects / "blob").write_bytes(b"x" * 1024)
    return git_dir


def _run(vault: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--vault", str(vault), *extra],
        capture_output=True,
        text=True,
        timeout=20,
    )


class TestReadonlyReport:
    def test_reports_size_without_deleting(self, tmp_path: Path):
        git_dir = _fake_git_dir(tmp_path)
        result = _run(tmp_path)
        assert result.returncode == 0, result.stderr
        assert "--confirm" in result.stdout
        assert git_dir.exists(), "dry-run must not delete .git"

    def test_noop_when_missing(self, tmp_path: Path):
        result = _run(tmp_path)
        assert result.returncode == 0
        assert "nothing to do" in result.stdout


class TestConfirmDeletes:
    def test_removes_git_dir(self, tmp_path: Path):
        git_dir = _fake_git_dir(tmp_path)
        result = _run(tmp_path, "--confirm")
        assert result.returncode == 0, result.stderr
        assert not git_dir.exists()
        # Vault structure outside .git is untouched — the script must never
        # touch user content.
        (tmp_path / "notes.md").write_text("hello")  # sanity: tmp_path still writable

    def test_preserves_user_data(self, tmp_path: Path):
        _fake_git_dir(tmp_path)
        note = tmp_path / "brain" / "status.md"
        note.parent.mkdir(parents=True)
        note.write_text("# status\n- keep me\n")

        _run(tmp_path, "--confirm")

        assert note.read_text() == "# status\n- keep me\n"


class TestRefusesWorktreeFile:
    def test_refuses_git_file(self, tmp_path: Path):
        # Linked worktrees use `.git` as a FILE, not a directory.
        (tmp_path / ".git").write_text("gitdir: /elsewhere/.git/worktrees/foo\n")

        result = _run(tmp_path, "--confirm")
        assert result.returncode == 1
        assert "not a directory" in result.stderr


class TestUsage:
    def test_missing_vault_errors(self, tmp_path: Path):
        fake = tmp_path / "nope"
        result = _run(fake)
        assert result.returncode == 1
        assert "vault not found" in result.stderr
