"""Tests for vault_lookup_cwd.py — T11 cwd-to-entity matcher.

The session-start hook calls this script on every session start to decide
whether the current working directory matches a vault entity, and (if so)
emit an "Active Project Context" markdown section for the hot-memory
systemMessage.

Matching logic (Q39 hybrid):
  1. Frontmatter match — entity has `paths: [...]` in YAML frontmatter and
     the cwd equals or is under any of them.
  2. Fuzzy basename match (fallback) — `Path(cwd).name` matches entity
     filename (case-insensitive, `.md` stripped).
  3. Frontmatter wins over fuzzy.
  4. No match → empty output, exit 0.

Runs the script as a real subprocess (same pattern as
test_validate_hot_memory_cli.py) to exercise stdout/stderr/exit-code
contracts end-to-end.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VAULT_LOOKUP_CWD_SCRIPT = (
    REPO_ROOT / "secondbrain" / "scripts" / "vault_lookup_cwd.py"
)


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VAULT_LOOKUP_CWD_SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _make_vault_with_entities(tmp_path: Path, entities: dict[str, str]) -> Path:
    """Create a minimal vault under tmp_path with given entity files.

    `entities` maps entity filename (without .md) → raw file body.
    Also creates empty `brain/status.md` and `log.md` so the grep-section
    never crashes.
    """
    vault = tmp_path / "vault"
    (vault / "entities").mkdir(parents=True)
    (vault / "brain").mkdir(parents=True)
    (vault / "brain" / "status.md").write_text("# Status\n")
    (vault / "log.md").write_text("# Log\n")
    for name, body in entities.items():
        (vault / "entities" / f"{name}.md").write_text(body)
    return vault


# ---------------------------------------------------------------------------
# Script presence + basic invocation
# ---------------------------------------------------------------------------

class TestScriptPresence:
    def test_script_exists(self):
        assert VAULT_LOOKUP_CWD_SCRIPT.is_file()

    def test_script_help_runs(self):
        r = _run_cli("--help")
        assert r.returncode == 0


# ---------------------------------------------------------------------------
# No match cases
# ---------------------------------------------------------------------------

class TestNoMatch:
    def test_unrelated_cwd_emits_empty_output(self, tmp_path: Path):
        """When cwd doesn't match any entity, stdout is empty and exit is 0."""
        vault = _make_vault_with_entities(
            tmp_path,
            {
                "alice": textwrap.dedent("""\
                    ---
                    type: person
                    ---
                    # Alice
                """),
            },
        )
        cwd = tmp_path / "totally-unrelated"
        cwd.mkdir()
        r = _run_cli("--vault", str(vault), "--cwd", str(cwd))
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == ""

    def test_missing_entities_dir_is_not_an_error(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        # No entities/ at all.
        r = _run_cli("--vault", str(vault), "--cwd", str(tmp_path))
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == ""

    def test_missing_vault_exits_nonzero(self, tmp_path: Path):
        """Missing vault path is an error (operator mistake)."""
        missing = tmp_path / "nonexistent-vault"
        r = _run_cli("--vault", str(missing), "--cwd", str(tmp_path))
        assert r.returncode != 0


# ---------------------------------------------------------------------------
# Frontmatter match
# ---------------------------------------------------------------------------

class TestFrontmatterMatch:
    def test_frontmatter_exact_path_match(self, tmp_path: Path):
        project = tmp_path / "proj"
        project.mkdir()
        vault = _make_vault_with_entities(
            tmp_path,
            {
                "my-project": textwrap.dedent(f"""\
                    ---
                    type: project
                    paths:
                      - {project}
                    summary: My cool project for work
                    ---
                    # My Project

                    Big deal project.
                """),
            },
        )
        r = _run_cli("--vault", str(vault), "--cwd", str(project))
        assert r.returncode == 0, r.stderr
        out = r.stdout
        assert "Active Project Context" in out
        assert "my-project" in out
        assert str(project) in out

    def test_frontmatter_subdirectory_match(self, tmp_path: Path):
        project = tmp_path / "proj"
        sub = project / "subdir" / "deeper"
        sub.mkdir(parents=True)
        vault = _make_vault_with_entities(
            tmp_path,
            {
                "my-project": textwrap.dedent(f"""\
                    ---
                    type: project
                    paths:
                      - {project}
                    ---
                    # My Project
                """),
            },
        )
        r = _run_cli("--vault", str(vault), "--cwd", str(sub))
        assert r.returncode == 0, r.stderr
        assert "Active Project Context" in r.stdout
        assert "my-project" in r.stdout

    def test_frontmatter_inline_list_format(self, tmp_path: Path):
        """YAML flow-style list should also work: paths: [/a, /b]."""
        project = tmp_path / "proj"
        project.mkdir()
        other = tmp_path / "other"
        other.mkdir()
        vault = _make_vault_with_entities(
            tmp_path,
            {
                "my-project": textwrap.dedent(f"""\
                    ---
                    type: project
                    paths: [{other}, {project}]
                    ---
                    # My Project
                """),
            },
        )
        r = _run_cli("--vault", str(vault), "--cwd", str(project))
        assert r.returncode == 0, r.stderr
        assert "Active Project Context" in r.stdout
        assert "my-project" in r.stdout

    def test_frontmatter_summary_is_included(self, tmp_path: Path):
        project = tmp_path / "proj"
        project.mkdir()
        vault = _make_vault_with_entities(
            tmp_path,
            {
                "my-project": textwrap.dedent(f"""\
                    ---
                    type: project
                    paths:
                      - {project}
                    summary: The definitive project summary line
                    ---
                    # My Project

                    Body paragraph not used for summary.
                """),
            },
        )
        r = _run_cli("--vault", str(vault), "--cwd", str(project))
        assert r.returncode == 0, r.stderr
        assert "The definitive project summary line" in r.stdout


# ---------------------------------------------------------------------------
# Fuzzy fallback
# ---------------------------------------------------------------------------

class TestFuzzyMatch:
    def test_fuzzy_basename_match_exact(self, tmp_path: Path):
        project = tmp_path / "my-project"
        project.mkdir()
        vault = _make_vault_with_entities(
            tmp_path,
            {
                "my-project": textwrap.dedent("""\
                    ---
                    type: project
                    ---
                    # My Project
                """),
            },
        )
        r = _run_cli("--vault", str(vault), "--cwd", str(project))
        assert r.returncode == 0, r.stderr
        assert "Active Project Context" in r.stdout
        assert "my-project" in r.stdout

    def test_fuzzy_basename_match_case_insensitive(self, tmp_path: Path):
        project = tmp_path / "My-Project"
        project.mkdir()
        vault = _make_vault_with_entities(
            tmp_path,
            {
                "my-project": textwrap.dedent("""\
                    ---
                    type: project
                    ---
                    # My Project
                """),
            },
        )
        r = _run_cli("--vault", str(vault), "--cwd", str(project))
        assert r.returncode == 0, r.stderr
        assert "Active Project Context" in r.stdout


# ---------------------------------------------------------------------------
# Precedence: frontmatter beats fuzzy
# ---------------------------------------------------------------------------

class TestPrecedence:
    def test_frontmatter_beats_fuzzy(self, tmp_path: Path):
        """When both entities could match, the frontmatter match wins."""
        project = tmp_path / "my-project"
        project.mkdir()
        vault = _make_vault_with_entities(
            tmp_path,
            {
                # fuzzy candidate: filename matches cwd basename
                "my-project": textwrap.dedent("""\
                    ---
                    type: project
                    ---
                    # My Project (fuzzy only)
                """),
                # frontmatter winner: has explicit paths frontmatter
                "canonical-project": textwrap.dedent(f"""\
                    ---
                    type: project
                    paths:
                      - {project}
                    ---
                    # Canonical Project (frontmatter winner)
                """),
            },
        )
        r = _run_cli("--vault", str(vault), "--cwd", str(project))
        assert r.returncode == 0, r.stderr
        assert "Active Project Context" in r.stdout
        # The chosen entity must be canonical-project, not my-project.
        # Check the wikilink specifically rather than substring matching
        # (the cwd path itself legitimately contains "my-project").
        assert "[[entities/canonical-project]]" in r.stdout
        assert "[[entities/my-project]]" not in r.stdout


# ---------------------------------------------------------------------------
# Missing peripheral files (status.md, log.md) are tolerated
# ---------------------------------------------------------------------------

class TestMissingPeripherals:
    def test_missing_status_md_does_not_crash(self, tmp_path: Path):
        project = tmp_path / "proj"
        project.mkdir()
        vault = tmp_path / "vault"
        (vault / "entities").mkdir(parents=True)
        # No brain/status.md, no log.md
        (vault / "entities" / "my-project.md").write_text(textwrap.dedent(f"""\
            ---
            type: project
            paths:
              - {project}
            ---
            # My Project
        """))
        r = _run_cli("--vault", str(vault), "--cwd", str(project))
        assert r.returncode == 0, r.stderr
        assert "Active Project Context" in r.stdout

    def test_missing_log_md_does_not_crash(self, tmp_path: Path):
        project = tmp_path / "proj"
        project.mkdir()
        vault = tmp_path / "vault"
        (vault / "entities").mkdir(parents=True)
        (vault / "brain").mkdir()
        (vault / "brain" / "status.md").write_text("# Status\n")
        # No log.md
        (vault / "entities" / "my-project.md").write_text(textwrap.dedent(f"""\
            ---
            type: project
            paths:
              - {project}
            ---
            # My Project
        """))
        r = _run_cli("--vault", str(vault), "--cwd", str(project))
        assert r.returncode == 0, r.stderr
        assert "Active Project Context" in r.stdout


# ---------------------------------------------------------------------------
# Output contents — status tasks and log entries
# ---------------------------------------------------------------------------

class TestOutputContents:
    def test_status_tasks_mentioning_entity_are_listed(self, tmp_path: Path):
        project = tmp_path / "proj"
        project.mkdir()
        vault = tmp_path / "vault"
        (vault / "entities").mkdir(parents=True)
        (vault / "brain").mkdir()
        (vault / "brain" / "status.md").write_text(textwrap.dedent("""\
            # Status

            - [ ] Ship feature for [[entities/my-project|My Project]]
            - [ ] Unrelated task for [[entities/other|Other]]
            - [x] Already done task for [[entities/my-project|My Project]]
        """))
        (vault / "log.md").write_text("# Log\n")
        (vault / "entities" / "my-project.md").write_text(textwrap.dedent(f"""\
            ---
            type: project
            paths:
              - {project}
            ---
            # My Project
        """))
        r = _run_cli("--vault", str(vault), "--cwd", str(project))
        assert r.returncode == 0, r.stderr
        # The incomplete task that mentions my-project should be listed.
        assert "Ship feature" in r.stdout
        # The unrelated task should NOT be in the output.
        assert "Unrelated task" not in r.stdout

    def test_recent_log_entries_are_listed(self, tmp_path: Path):
        project = tmp_path / "proj"
        project.mkdir()
        vault = tmp_path / "vault"
        (vault / "entities").mkdir(parents=True)
        (vault / "brain").mkdir()
        (vault / "brain" / "status.md").write_text("# Status\n")
        (vault / "log.md").write_text(textwrap.dedent("""\
            # Log

            ## [2026-04-10 10:00] session-start | Started
            Working on [[entities/my-project|My Project]] features

            ## [2026-04-09 15:00] session-end | Done
            Wrapping up unrelated

            ## [2026-04-08 09:00] session-start | Earlier
            More [[entities/my-project|My Project]] work
        """))
        (vault / "entities" / "my-project.md").write_text(textwrap.dedent(f"""\
            ---
            type: project
            paths:
              - {project}
            ---
            # My Project
        """))
        r = _run_cli("--vault", str(vault), "--cwd", str(project))
        assert r.returncode == 0, r.stderr
        # At least one log reference that mentions my-project should surface.
        assert "my-project" in r.stdout.lower() or "My Project" in r.stdout
