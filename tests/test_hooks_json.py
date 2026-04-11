"""Structural tests for secondbrain/hooks/hooks.json.

These tests enforce the shape of the hook wiring so Phase 2/3 can't silently
drop or rename a matcher without tripping a test. They complement
test_plugin_manifest.py, which only checks schema-level invariants.
"""

from __future__ import annotations

import json
from pathlib import Path

HOOKS_JSON = (
    Path(__file__).resolve().parent.parent
    / "secondbrain"
    / "hooks"
    / "hooks.json"
)


def _load() -> dict:
    return json.loads(HOOKS_JSON.read_text())


def _matchers(event: str) -> list[str]:
    data = _load()
    return [entry["matcher"] for entry in data["hooks"].get(event, [])]


# ---------------------------------------------------------------------------
# Top-level shape
# ---------------------------------------------------------------------------

class TestTopLevelShape:
    def test_json_parses(self):
        assert _load()  # raises on parse error

    def test_required_event_keys(self):
        data = _load()
        for key in ("SessionStart", "SessionEnd", "PreToolUse", "PostToolUse", "Stop"):
            assert key in data["hooks"], f"hooks.json missing required event: {key}"


# ---------------------------------------------------------------------------
# PreToolUse — must have enforce-mcp-only, enforce-immutability,
# enforce-immutability-bash matchers
# ---------------------------------------------------------------------------

class TestPreToolUse:
    def test_has_at_least_three_matchers(self):
        assert len(_matchers("PreToolUse")) >= 3, (
            "PreToolUse must wire enforce-mcp-only, enforce-immutability, "
            "and enforce-immutability-bash"
        )

    def test_mcp_only_matcher_wired(self):
        """enforce-mcp-only.sh must fire for Edit/Write/NotebookEdit/Bash."""
        data = _load()
        found = False
        for entry in data["hooks"]["PreToolUse"]:
            matcher = entry.get("matcher", "")
            cmds = [h.get("command", "") for h in entry.get("hooks", [])]
            if any("enforce-mcp-only.sh" in c for c in cmds):
                found = True
                # Matcher must cover Edit/Write/NotebookEdit/Bash.
                for tool in ("Edit", "Write", "NotebookEdit", "Bash"):
                    assert tool in matcher, (
                        f"enforce-mcp-only matcher must include {tool}, got {matcher!r}"
                    )
        assert found, "PreToolUse is missing a matcher wired to enforce-mcp-only.sh"

    def test_immutability_mcp_matcher_preserved(self):
        """The existing enforce-immutability.sh wiring for MCP vault_* is preserved."""
        data = _load()
        found = False
        for entry in data["hooks"]["PreToolUse"]:
            cmds = [h.get("command", "") for h in entry.get("hooks", [])]
            if any("enforce-immutability.sh" in c for c in cmds):
                found = True
                matcher = entry.get("matcher", "")
                assert "mcp__obsidian__vault_" in matcher, (
                    f"enforce-immutability.sh matcher must target MCP vault writes, got {matcher!r}"
                )
        assert found, "PreToolUse is missing the enforce-immutability.sh wiring"

    def test_immutability_bash_matcher_preserved(self):
        """The existing enforce-immutability-bash.sh wiring for Bash is preserved."""
        data = _load()
        found = False
        for entry in data["hooks"]["PreToolUse"]:
            cmds = [h.get("command", "") for h in entry.get("hooks", [])]
            if any("enforce-immutability-bash.sh" in c for c in cmds):
                found = True
                matcher = entry.get("matcher", "")
                assert "Bash" in matcher, (
                    f"enforce-immutability-bash.sh matcher must include Bash, got {matcher!r}"
                )
        assert found, "PreToolUse is missing the enforce-immutability-bash.sh wiring"


# ---------------------------------------------------------------------------
# PostToolUse — matcher must include |Bash
# ---------------------------------------------------------------------------

class TestPostToolUse:
    def test_matcher_includes_bash(self):
        data = _load()
        found_validator = False
        for entry in data["hooks"]["PostToolUse"]:
            cmds = [h.get("command", "") for h in entry.get("hooks", [])]
            if any("validate-after-write.sh" in c for c in cmds):
                found_validator = True
                matcher = entry.get("matcher", "")
                assert "Bash" in matcher, (
                    f"validate-after-write.sh matcher must include Bash after Phase 1, "
                    f"got {matcher!r}"
                )
                assert "mcp__obsidian__vault_" in matcher, (
                    f"validate-after-write.sh matcher must still include MCP vault writes, "
                    f"got {matcher!r}"
                )
        assert found_validator, "PostToolUse is missing validate-after-write.sh wiring"


# ---------------------------------------------------------------------------
# Stop — new matcher points at on-stop.sh stub
# ---------------------------------------------------------------------------

class TestStopMatcher:
    def test_stop_matcher_exists(self):
        data = _load()
        assert "Stop" in data["hooks"], "hooks.json missing Stop event"
        assert data["hooks"]["Stop"], "Stop event has no entries"

    def test_stop_matcher_points_at_on_stop_sh(self):
        data = _load()
        found = False
        for entry in data["hooks"]["Stop"]:
            for hook in entry.get("hooks", []):
                if "on-stop.sh" in hook.get("command", ""):
                    found = True
        assert found, "Stop matcher must wire on-stop.sh"

    def test_stop_matcher_is_empty_string(self):
        """Match all stops; the stub will route once real logic lands."""
        data = _load()
        matchers = [entry.get("matcher") for entry in data["hooks"]["Stop"]]
        assert any(m == "" for m in matchers), (
            f"Stop should have an empty-string matcher to match all stops; got {matchers!r}"
        )


# ---------------------------------------------------------------------------
# SessionStart / SessionEnd — must remain unchanged (Phase 3 owns the rewrite)
# ---------------------------------------------------------------------------

class TestSessionHooksUntouched:
    def test_session_start_still_wires_session_start_sh(self):
        data = _load()
        found = False
        for entry in data["hooks"]["SessionStart"]:
            for hook in entry.get("hooks", []):
                if "session-start.sh" in hook.get("command", ""):
                    found = True
        assert found, "SessionStart must still wire session-start.sh in Phase 1"

    def test_session_end_still_wires_session_end_sh(self):
        data = _load()
        found = False
        for entry in data["hooks"]["SessionEnd"]:
            for hook in entry.get("hooks", []):
                if "session-end.sh" in hook.get("command", ""):
                    found = True
        assert found, "SessionEnd must still wire session-end.sh in Phase 1"

    def test_emit_hot_memory_sh_not_wired_yet(self):
        """Phase 1 must not reference emit-hot-memory.sh — that's Phase 3's job."""
        data = _load()
        all_commands: list[str] = []
        for entries in data["hooks"].values():
            for entry in entries:
                for hook in entry.get("hooks", []):
                    all_commands.append(hook.get("command", ""))
        for cmd in all_commands:
            assert "emit-hot-memory.sh" not in cmd, (
                f"Phase 1 must not wire emit-hot-memory.sh (that's Phase 3); "
                f"found in command: {cmd!r}"
            )
