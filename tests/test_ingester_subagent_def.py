"""String-contract tests for the secondbrain-ingester subagent definition.

T13 ships a custom subagent definition at
    secondbrain/agents/secondbrain-ingester.md
which the Stop hook's detached `claude --agent secondbrain-ingester ...`
invocation loads. The subagent:

    1. Reads a context envelope JSON at a path supplied by the dispatcher.
    2. Routes new conversation turns into the vault (tasks, decisions,
       deadlines, entities, etc.).
    3. Builds a hot-memory update draft and applies it via update_hot_memory.py.
    4. Advances the per-session cursor via advance_cursor.py.
    5. Commits the result via vault_git.py commit-stop.
    6. NEVER talks to the user.

This test file locks the contract of that definition file. Drift kills
background ingest silently — the Stop hook returns success, the detached
subprocess runs, and if the subagent is misconfigured nothing reaches the
vault. These contracts are the last line of defense.

Scope of what we enforce (deliberately narrow — we're not reviewing the
prose, we're enforcing the shape):

    - File exists at the expected plugin-relative path.
    - Frontmatter is parseable YAML-ish (grep-based — no yaml import).
    - Frontmatter declares a tool allowlist including every MCP vault tool
      the subagent needs. It does NOT need Edit/Write/Task — those are
      forbidden and MUST appear in disallowedTools.
    - Body mentions the script commands the subagent must call
      (update_hot_memory.py --apply, advance_cursor.py, vault_git.py
      commit-stop).
    - Body documents the hot-memory update sections (Top Deadlines,
      Urgent This Week, Recent Activity).
    - Body documents the cursor-advancement discipline.
    - "Forbidden actions" section mentions writing hot-memory directly
      via MCP (must use the script) and talking to the user.
"""

from __future__ import annotations

from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent / "secondbrain"
AGENT_DEF = PLUGIN_ROOT / "agents" / "secondbrain-ingester.md"


@pytest.fixture(scope="module")
def agent_text() -> str:
    assert AGENT_DEF.is_file(), (
        f"secondbrain-ingester subagent definition must exist at {AGENT_DEF}; "
        f"create it during T13."
    )
    return AGENT_DEF.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def frontmatter(agent_text: str) -> str:
    """Return the YAML-ish frontmatter block verbatim."""
    assert agent_text.startswith("---\n"), (
        "secondbrain-ingester.md must start with a '---' frontmatter block; "
        "Claude Code subagent definitions require one."
    )
    end = agent_text.find("\n---", 4)
    assert end > 0, "frontmatter block is unterminated"
    return agent_text[4:end]


@pytest.fixture(scope="module")
def body(agent_text: str) -> str:
    """Return everything after the frontmatter."""
    assert agent_text.startswith("---\n")
    end = agent_text.find("\n---", 4)
    assert end > 0
    return agent_text[end + 4 :]


class TestFilePresence:
    def test_file_exists(self):
        assert AGENT_DEF.is_file(), (
            f"secondbrain-ingester subagent definition missing at {AGENT_DEF}"
        )

    def test_file_is_markdown(self):
        assert AGENT_DEF.suffix == ".md"

    def test_file_not_empty(self, agent_text: str):
        # Pick a reasonable floor — the spec has a lot of rules to encode.
        assert len(agent_text) > 500, (
            f"secondbrain-ingester.md is suspiciously short ({len(agent_text)} "
            f"bytes); the spec requires routing rules, hot-memory rules, "
            f"cursor discipline, and forbidden actions."
        )


class TestFrontmatterFields:
    def test_frontmatter_has_name(self, frontmatter: str):
        assert "name:" in frontmatter
        # Must be exactly 'secondbrain-ingester' to match the --agent flag.
        assert "secondbrain-ingester" in frontmatter, (
            "frontmatter name must be 'secondbrain-ingester' — the Stop hook "
            "dispatches via `claude --agent secondbrain-ingester`."
        )

    def test_frontmatter_has_description(self, frontmatter: str):
        assert "description:" in frontmatter

    def test_frontmatter_has_tools_allowlist(self, frontmatter: str):
        assert "tools:" in frontmatter, (
            "frontmatter must declare a 'tools:' list — subagent tool "
            "allowlist. Claude Code will otherwise grant broad access."
        )

    def test_tools_allowlist_has_read(self, frontmatter: str):
        # Subagent MUST be able to read the envelope file.
        assert "Read" in frontmatter

    def test_tools_allowlist_has_bash(self, frontmatter: str):
        # Subagent calls update_hot_memory.py / advance_cursor.py / vault_git.py
        # via Bash.
        assert "Bash" in frontmatter

    def test_tools_allowlist_has_mcp_vault_create(self, frontmatter: str):
        assert "mcp__obsidian__vault_create" in frontmatter

    def test_tools_allowlist_has_mcp_vault_update(self, frontmatter: str):
        assert "mcp__obsidian__vault_update" in frontmatter

    def test_tools_allowlist_has_mcp_vault_patch(self, frontmatter: str):
        assert "mcp__obsidian__vault_patch" in frontmatter

    def test_tools_allowlist_has_mcp_vault_edit(self, frontmatter: str):
        assert "mcp__obsidian__vault_edit" in frontmatter

    def test_tools_allowlist_has_mcp_vault_read(self, frontmatter: str):
        assert "mcp__obsidian__vault_read" in frontmatter

    def test_tools_allowlist_has_mcp_vault_list(self, frontmatter: str):
        assert "mcp__obsidian__vault_list" in frontmatter

    def test_tools_allowlist_has_mcp_vault_search(self, frontmatter: str):
        assert "mcp__obsidian__vault_search" in frontmatter

    def test_tools_allowlist_has_dataview_query(self, frontmatter: str):
        assert "mcp__obsidian__dataview_query" in frontmatter

    def test_frontmatter_has_disallowed_tools(self, frontmatter: str):
        assert "disallowedTools:" in frontmatter, (
            "frontmatter must declare 'disallowedTools:' — explicit denylist "
            "of filesystem writes (Edit/Write/NotebookEdit) and subagent "
            "recursion (Task)."
        )

    def test_disallowed_tools_includes_edit(self, frontmatter: str):
        # The ingester MUST NOT touch the host filesystem via Edit.
        # All vault writes go through MCP.
        idx = frontmatter.find("disallowedTools:")
        assert idx >= 0
        tail = frontmatter[idx:]
        assert "Edit" in tail

    def test_disallowed_tools_includes_write(self, frontmatter: str):
        idx = frontmatter.find("disallowedTools:")
        assert idx >= 0
        tail = frontmatter[idx:]
        assert "Write" in tail

    def test_disallowed_tools_includes_notebook_edit(self, frontmatter: str):
        idx = frontmatter.find("disallowedTools:")
        assert idx >= 0
        tail = frontmatter[idx:]
        assert "NotebookEdit" in tail

    def test_disallowed_tools_includes_task(self, frontmatter: str):
        # Subagents can't spawn subagents. Lock it down explicitly so a
        # prompt-injection attempt can't recruit the ingester to fan out.
        idx = frontmatter.find("disallowedTools:")
        assert idx >= 0
        tail = frontmatter[idx:]
        assert "Task" in tail


class TestBodyMentionsContextEnvelope:
    def test_body_mentions_envelope(self, body: str):
        assert "envelope" in body.lower(), (
            "body must describe how to consume the context envelope file "
            "(the dispatcher passes an envelope path in the -p prompt)."
        )

    def test_body_mentions_new_turns(self, body: str):
        assert "new_turns" in body, (
            "body must reference `new_turns` — the envelope's list of "
            "conversation turns to ingest."
        )


class TestBodyMentionsRoutingRules:
    def test_body_mentions_tasks_to_status(self, body: str):
        low = body.lower()
        assert "tasks" in low
        assert "status.md" in low

    def test_body_mentions_deadlines(self, body: str):
        assert "deadlines" in body.lower()

    def test_body_mentions_decisions(self, body: str):
        assert "decisions" in body.lower()

    def test_body_mentions_entities(self, body: str):
        assert "entities" in body.lower()

    def test_body_mentions_wikilinks(self, body: str):
        # Either prose "wikilink" or the [[ literal — both accepted.
        low = body.lower()
        assert "wikilink" in low or "[[" in body


class TestBodyMentionsHotMemoryRules:
    def test_body_mentions_top_deadlines_section(self, body: str):
        assert "Top Deadlines" in body, (
            "hot-memory update rules must mention the 'Top Deadlines' "
            "section — this is one of the reasoning-layer outputs."
        )

    def test_body_mentions_urgent_this_week_section(self, body: str):
        assert "Urgent This Week" in body

    def test_body_mentions_recent_activity(self, body: str):
        assert "Recent Activity" in body

    def test_body_mentions_update_hot_memory_script(self, body: str):
        assert "update_hot_memory.py" in body, (
            "body must name the update_hot_memory.py script — hot-memory "
            "updates MUST go through the script, not direct MCP writes."
        )

    def test_body_mentions_apply_flag(self, body: str):
        assert "--apply" in body, (
            "update_hot_memory.py is invoked with --apply <draft.json>; "
            "the subagent must know to use that flag."
        )


class TestBodyMentionsCursorAdvancement:
    def test_body_mentions_advance_cursor_script(self, body: str):
        assert "advance_cursor.py" in body

    def test_body_mentions_cursor(self, body: str):
        assert "cursor" in body.lower()

    def test_body_mentions_message_uuid(self, body: str):
        # Used as --to-message-uuid when calling advance_cursor.py
        low = body.lower()
        assert "message-uuid" in low or "to-message-uuid" in low


class TestBodyMentionsCommit:
    def test_body_mentions_vault_git_commit_stop(self, body: str):
        assert "vault_git.py" in body
        assert "commit-stop" in body

    def test_body_mentions_author(self, body: str):
        # --author flag is required for the ingester's commits.
        assert "--author" in body


class TestBodyForbiddenActions:
    def test_body_has_forbidden_section(self, body: str):
        assert "Forbidden" in body or "forbidden" in body

    def test_body_forbids_direct_hot_memory_write(self, body: str):
        low = body.lower()
        # Must tell the subagent not to write hot-memory.md directly via MCP.
        assert "hot-memory" in low
        # We expect some form of "do not write hot-memory directly" guidance.
        assert (
            "direct" in low
            or "never write" in low
            or "not write hot-memory" in low
            or "must use update_hot_memory" in low
            or "via update_hot_memory" in low
        ), (
            "body must forbid direct hot-memory.md writes via MCP — use "
            "update_hot_memory.py --apply instead."
        )

    def test_body_forbids_talking_to_user(self, body: str):
        low = body.lower()
        assert "user" in low
        # Accept any phrasing that says "never talk to the user" / "no
        # user-facing output" / "silent".
        assert (
            "never talk" in low
            or "no user" in low
            or "not talk to the user" in low
            or "do not talk" in low
            or "silent" in low
        )
