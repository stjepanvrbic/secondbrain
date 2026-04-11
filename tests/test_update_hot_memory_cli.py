"""Tests for update_hot_memory.py — the ONLY writer of brain/hot-memory.md.

Two modes:
  --regenerate --vault <path>: assemble a fresh hot-memory from current vault
    state (me/profile.md, brain/status.md, brain/deadlines.md, tail of log.md)
    and write it via Connect MCP.
  --apply <draft.json> --vault <path>: apply section updates from a JSON
    draft (produced by the ingest subagent in T13) to the existing file.

Tests mock `ConnectMCPClient` via dependency injection — `update_hot_memory.py`
accepts an optional `client_factory` argument so tests can inject a fake. This
lets us exercise the real CLI entry point end-to-end without touching the
network.

Contract enforced by the tests:
  - `--regenerate` must build a valid hot-memory and call `vault_update`.
  - `--apply` must preserve unchanged sections, replace the targeted ones,
    and refuse to write if the result fails validation (token budget, missing
    required section, etc.).
  - MCP unreachable → exit non-zero, no write attempted.
  - Neither mode leaves a partial file (we check vault_update was either
    called exactly once with a valid document or not called at all).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "secondbrain" / "scripts"
UPDATE_HOT_MEMORY_SCRIPT = SCRIPTS_DIR / "update_hot_memory.py"

sys.path.insert(0, str(SCRIPTS_DIR))

import update_hot_memory  # type: ignore[reportMissingImports]
from connect_mcp_client import (  # type: ignore[reportMissingImports]
    ConnectMCPUnreachable,
)
from hot_memory_schema import (  # type: ignore[reportMissingImports]
    INITIAL_TEMPLATE,
    REQUIRED_SECTIONS,
    TOKEN_HARD_LIMIT,
    parse_sections,
    validate,
)


# ---------------------------------------------------------------------------
# Fake MCP client
# ---------------------------------------------------------------------------

class FakeMCPClient:
    """In-memory stand-in for ConnectMCPClient used by update_hot_memory.

    Stores a dict of `path → content`. `vault_read` and `vault_update`
    read/write that dict. Every call is recorded for test assertions.
    """

    def __init__(self, files: Dict[str, str]):
        self._files: Dict[str, str] = dict(files)
        self.read_calls: List[str] = []
        self.update_calls: List[Dict[str, str]] = []
        self.create_calls: List[Dict[str, str]] = []

    def vault_read(self, path: str) -> str:
        self.read_calls.append(path)
        if path not in self._files:
            # Mirror ConnectMCPClient's behavior: raise ConnectMCPNotFound
            # on missing files. We use a plain exception here since we
            # don't want to pull in the full exception hierarchy.
            from connect_mcp_client import ConnectMCPNotFound  # type: ignore[reportMissingImports]
            raise ConnectMCPNotFound(f"file not found: {path}")
        return self._files[path]

    def vault_update(self, path: str, content: str) -> dict:
        self.update_calls.append({"path": path, "content": content})
        self._files[path] = content
        return {"success": True}

    def vault_create(self, path: str, content: str) -> dict:
        self.create_calls.append({"path": path, "content": content})
        self._files[path] = content
        return {"success": True}

    # ----- test helpers -----

    def get(self, path: str) -> str:
        return self._files.get(path, "")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def healthy_vault_files() -> Dict[str, str]:
    """A minimal set of vault files the regenerator reads from."""
    return {
        "me/profile.md": (
            "---\nname: Test User\n---\n# Profile\n\nName: Test User\n"
            "Roles: engineer, manager\n"
        ),
        "brain/status.md": (
            "---\nupdated: 2026-04-11\n---\n# Status\n\n"
            "## Today's Plan — 2026-04-11\n\n"
            "- [ ] Review PR [[entities/alice|Alice]] [due:: 2026-04-11]\n"
            "- [ ] Finish report [due:: 2026-04-12]\n"
            "- [x] Send invoice [done:: 2026-04-10]\n"
        ),
        "brain/deadlines.md": (
            "# Deadlines\n\n"
            "- 2026-04-15: Project X kickoff\n"
            "- 2026-04-20: Tax filing\n"
        ),
        "log.md": (
            "# Log\n\n"
            "## [2026-04-10 09:00] session-start | Morning\n"
            "Started day planning.\n\n"
            "## [2026-04-10 15:00] session-start | Afternoon\n"
            "Finished the report.\n"
        ),
        "_MANIFEST.md": "# Vault Manifest\n\nFiles: 12\n",
    }


@pytest.fixture
def fake_client_factory(healthy_vault_files: Dict[str, str]):
    """Returns a callable that creates a fresh FakeMCPClient on each call."""
    client_holder: Dict[str, FakeMCPClient] = {}

    def factory() -> FakeMCPClient:
        client = FakeMCPClient(healthy_vault_files)
        client_holder["last"] = client
        return client

    factory.last = lambda: client_holder["last"]  # type: ignore[attr-defined]
    return factory


@pytest.fixture
def vault_with_initial_hot_memory(
    healthy_vault_files: Dict[str, str],
) -> Dict[str, str]:
    files = dict(healthy_vault_files)
    files["brain/hot-memory.md"] = INITIAL_TEMPLATE
    return files


# ---------------------------------------------------------------------------
# --regenerate happy path
# ---------------------------------------------------------------------------

class TestRegenerate:
    def test_regenerate_builds_valid_file(
        self,
        tmp_path: Path,
        healthy_vault_files: Dict[str, str],
    ):
        client = FakeMCPClient(healthy_vault_files)
        rc = update_hot_memory.main(
            argv=["--regenerate", "--vault", str(tmp_path)],
            client_factory=lambda: client,
        )
        assert rc == 0
        assert len(client.update_calls) == 1
        call = client.update_calls[0]
        assert call["path"] == "brain/hot-memory.md"
        # Validate the content that was written.
        result = validate(call["content"])
        assert result.ok, result.errors

    def test_regenerate_reads_vault_sources(
        self,
        tmp_path: Path,
        healthy_vault_files: Dict[str, str],
    ):
        client = FakeMCPClient(healthy_vault_files)
        update_hot_memory.main(
            argv=["--regenerate", "--vault", str(tmp_path)],
            client_factory=lambda: client,
        )
        # The regenerator should have read at least these sources.
        assert any("profile.md" in p for p in client.read_calls)
        assert any("status.md" in p for p in client.read_calls)
        assert any("deadlines.md" in p for p in client.read_calls)

    def test_regenerate_updates_generated_by_frontmatter(
        self,
        tmp_path: Path,
        healthy_vault_files: Dict[str, str],
    ):
        client = FakeMCPClient(healthy_vault_files)
        update_hot_memory.main(
            argv=["--regenerate", "--vault", str(tmp_path)],
            client_factory=lambda: client,
        )
        content = client.update_calls[0]["content"]
        assert "generated_by: update_hot_memory.py" in content or (
            "generated_by:" in content and "regenerate" in content.lower()
        )
        # Timestamp is an ISO-8601 string (has a T between date and time).
        for line in content.splitlines():
            if line.startswith("generated_at:"):
                assert "T" in line  # basic sanity — not 1970
                break
        else:
            pytest.fail("no generated_at line in frontmatter")

    def test_regenerate_prints_token_estimate(
        self,
        tmp_path: Path,
        healthy_vault_files: Dict[str, str],
        capsys: pytest.CaptureFixture[str],
    ):
        client = FakeMCPClient(healthy_vault_files)
        update_hot_memory.main(
            argv=["--regenerate", "--vault", str(tmp_path)],
            client_factory=lambda: client,
        )
        captured = capsys.readouterr()
        out = captured.out.lower()
        assert "token" in out or "ok" in out


# ---------------------------------------------------------------------------
# --regenerate on an empty vault (all source files missing)
# ---------------------------------------------------------------------------

class TestRegenerateEmptyVault:
    def test_regenerate_with_no_sources_still_produces_valid_file(
        self,
        tmp_path: Path,
    ):
        # Empty vault — the regenerator must fall back to placeholders
        # but still produce a document that passes validation.
        client = FakeMCPClient({})
        rc = update_hot_memory.main(
            argv=["--regenerate", "--vault", str(tmp_path)],
            client_factory=lambda: client,
        )
        assert rc == 0
        assert client.update_calls
        result = validate(client.update_calls[0]["content"])
        assert result.ok


# ---------------------------------------------------------------------------
# --regenerate when MCP is unreachable
# ---------------------------------------------------------------------------

class TestRegenerateMCPUnreachable:
    def test_unreachable_exits_non_zero_and_does_not_write(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ):
        def factory():
            raise ConnectMCPUnreachable("cannot reach server")

        rc = update_hot_memory.main(
            argv=["--regenerate", "--vault", str(tmp_path)],
            client_factory=factory,
        )
        assert rc != 0
        captured = capsys.readouterr()
        # The error should surface on stderr somewhere.
        assert "unreach" in captured.err.lower() or "mcp" in captured.err.lower()


# ---------------------------------------------------------------------------
# --apply: incremental section updates from a JSON draft
# ---------------------------------------------------------------------------

class TestApply:
    def test_apply_replaces_specified_section(
        self,
        tmp_path: Path,
        vault_with_initial_hot_memory: Dict[str, str],
    ):
        client = FakeMCPClient(vault_with_initial_hot_memory)

        draft = {
            "section_updates": [
                {
                    "section": "Top Deadlines",
                    "content": "- 2026-04-15 Project X kickoff\n- 2026-04-20 Tax filing",
                }
            ],
            "reason": "ingest subagent discovered 2 new deadlines",
        }
        draft_path = tmp_path / "draft.json"
        draft_path.write_text(json.dumps(draft))

        rc = update_hot_memory.main(
            argv=[
                "--apply", str(draft_path),
                "--vault", str(tmp_path),
            ],
            client_factory=lambda: client,
        )
        assert rc == 0
        assert len(client.update_calls) == 1
        written = client.update_calls[0]["content"]
        sections = parse_sections(written)
        assert "Project X kickoff" in sections["Top Deadlines"]
        # Non-targeted sections preserved.
        assert "Identity & Directive" in sections
        assert "Vault Layout" in sections

    def test_apply_preserves_other_sections(
        self,
        tmp_path: Path,
        vault_with_initial_hot_memory: Dict[str, str],
    ):
        client = FakeMCPClient(vault_with_initial_hot_memory)
        original = parse_sections(INITIAL_TEMPLATE)

        draft = {
            "section_updates": [
                {"section": "Urgent This Week", "content": "- Task A\n- Task B"}
            ],
            "reason": "updated task list",
        }
        draft_path = tmp_path / "draft.json"
        draft_path.write_text(json.dumps(draft))

        update_hot_memory.main(
            argv=[
                "--apply", str(draft_path),
                "--vault", str(tmp_path),
            ],
            client_factory=lambda: client,
        )

        written = client.update_calls[0]["content"]
        updated_sections = parse_sections(written)
        # Urgent This Week was replaced.
        assert "Task A" in updated_sections["Urgent This Week"]
        # Everything else unchanged.
        for name in REQUIRED_SECTIONS:
            if name == "Urgent This Week":
                continue
            assert updated_sections[name].strip() == original[name].strip(), (
                f"section {name!r} was modified when it should not have been"
            )

    def test_apply_draft_over_token_budget_fails(
        self,
        tmp_path: Path,
        vault_with_initial_hot_memory: Dict[str, str],
    ):
        client = FakeMCPClient(vault_with_initial_hot_memory)

        # A draft whose content, when substituted, makes the file exceed
        # the hard token limit.
        huge_content = "x" * (TOKEN_HARD_LIMIT * 4 + 1000)
        draft = {
            "section_updates": [
                {"section": "Recent Activity", "content": huge_content}
            ],
            "reason": "overflow",
        }
        draft_path = tmp_path / "draft.json"
        draft_path.write_text(json.dumps(draft))

        rc = update_hot_memory.main(
            argv=[
                "--apply", str(draft_path),
                "--vault", str(tmp_path),
            ],
            client_factory=lambda: client,
        )
        assert rc != 0
        # No write must have been attempted.
        assert client.update_calls == []
        # Vault file must still contain the original INITIAL_TEMPLATE.
        assert client.get("brain/hot-memory.md") == INITIAL_TEMPLATE

    def test_apply_missing_draft_file_fails(
        self,
        tmp_path: Path,
        vault_with_initial_hot_memory: Dict[str, str],
    ):
        client = FakeMCPClient(vault_with_initial_hot_memory)
        missing = tmp_path / "nonexistent-draft.json"
        rc = update_hot_memory.main(
            argv=[
                "--apply", str(missing),
                "--vault", str(tmp_path),
            ],
            client_factory=lambda: client,
        )
        assert rc != 0
        assert client.update_calls == []

    def test_apply_malformed_draft_fails(
        self,
        tmp_path: Path,
        vault_with_initial_hot_memory: Dict[str, str],
    ):
        client = FakeMCPClient(vault_with_initial_hot_memory)
        draft_path = tmp_path / "draft.json"
        draft_path.write_text("this is not json")
        rc = update_hot_memory.main(
            argv=[
                "--apply", str(draft_path),
                "--vault", str(tmp_path),
            ],
            client_factory=lambda: client,
        )
        assert rc != 0
        assert client.update_calls == []

    def test_apply_accepts_items_list_variant(
        self,
        tmp_path: Path,
        vault_with_initial_hot_memory: Dict[str, str],
    ):
        """T13 ingester emits `items: [...]` rather than `content: "..."`.
        Both shapes must be accepted so the contract is forgiving.
        """
        client = FakeMCPClient(vault_with_initial_hot_memory)
        draft = {
            "section_updates": [
                {
                    "section": "Top Deadlines",
                    "items": [
                        "- 2026-04-15 Project X kickoff",
                        "- 2026-04-20 Tax filing",
                    ],
                }
            ],
            "reason": "items-style draft",
        }
        draft_path = tmp_path / "draft.json"
        draft_path.write_text(json.dumps(draft))
        rc = update_hot_memory.main(
            argv=[
                "--apply", str(draft_path),
                "--vault", str(tmp_path),
            ],
            client_factory=lambda: client,
        )
        assert rc == 0
        written = client.update_calls[0]["content"]
        sections = parse_sections(written)
        assert "Project X kickoff" in sections["Top Deadlines"]
        assert "Tax filing" in sections["Top Deadlines"]

    def test_apply_when_hot_memory_missing_creates_it(
        self,
        tmp_path: Path,
        healthy_vault_files: Dict[str, str],
    ):
        # Vault has no hot-memory.md yet. --apply must start from a fresh
        # template so the ingester can still deposit updates.
        client = FakeMCPClient(healthy_vault_files)
        draft = {
            "section_updates": [
                {"section": "Top Deadlines", "content": "- 2026-04-15 Kickoff"}
            ],
            "reason": "bootstrap",
        }
        draft_path = tmp_path / "draft.json"
        draft_path.write_text(json.dumps(draft))

        rc = update_hot_memory.main(
            argv=[
                "--apply", str(draft_path),
                "--vault", str(tmp_path),
            ],
            client_factory=lambda: client,
        )
        assert rc == 0
        # Written via either create or update — we accept either.
        total_writes = len(client.update_calls) + len(client.create_calls)
        assert total_writes == 1


# ---------------------------------------------------------------------------
# --apply when MCP is unreachable
# ---------------------------------------------------------------------------

class TestApplyMCPUnreachable:
    def test_unreachable_exits_non_zero(
        self,
        tmp_path: Path,
    ):
        def factory():
            raise ConnectMCPUnreachable("cannot reach server")

        draft = {"section_updates": [], "reason": "noop"}
        draft_path = tmp_path / "draft.json"
        draft_path.write_text(json.dumps(draft))

        rc = update_hot_memory.main(
            argv=[
                "--apply", str(draft_path),
                "--vault", str(tmp_path),
            ],
            client_factory=factory,
        )
        assert rc != 0


# ---------------------------------------------------------------------------
# CLI flag validation
# ---------------------------------------------------------------------------

class TestCLIFlags:
    def test_missing_mode_fails(
        self,
        tmp_path: Path,
    ):
        rc = update_hot_memory.main(
            argv=["--vault", str(tmp_path)],
            client_factory=lambda: FakeMCPClient({}),
        )
        assert rc != 0

    def test_both_modes_at_once_fails(
        self,
        tmp_path: Path,
    ):
        draft_path = tmp_path / "draft.json"
        draft_path.write_text(json.dumps({"section_updates": [], "reason": "x"}))
        rc = update_hot_memory.main(
            argv=[
                "--regenerate",
                "--apply", str(draft_path),
                "--vault", str(tmp_path),
            ],
            client_factory=lambda: FakeMCPClient({}),
        )
        assert rc != 0

    def test_missing_vault_arg_fails(
        self,
    ):
        rc = update_hot_memory.main(
            argv=["--regenerate"],
            client_factory=lambda: FakeMCPClient({}),
        )
        assert rc != 0
