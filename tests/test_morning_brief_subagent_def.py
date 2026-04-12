"""String-contract tests for the secondbrain-morning-brief subagent definition.

T14 ships a new plugin-bundled subagent at
    secondbrain/agents/secondbrain-morning-brief.md

It runs at 08:00 daily via a scheduled task and writes a cached
`brain/morning-brief.md` file — a snapshot the user reads on demand
(via whats-next or similar) without the morning dispatch skill having
to rebuild context from scratch.

Scope of what we enforce:

    - File exists at the expected plugin-relative path
    - Frontmatter declares a name, description, tools allowlist, and
      disallowedTools denylist
    - Tools allowlist includes the MCP vault tools the subagent needs
      for reading inbox, status, deadlines, decisions and for writing
      the brief
    - disallowedTools includes Edit/Write/NotebookEdit (host-filesystem
      writes forbidden), Task (no subagent recursion), WebFetch/WebSearch
    - Body mentions brain/morning-brief.md as the output file
    - Body mentions inbox scanning
    - Body mentions deadlines + status surfacing
    - Forbidden Actions section mentions no user interaction and no
      direct hot-memory writes

Scope of what we DO NOT enforce: the exact prose, tone, or length of
the subagent prompt — that's prose the maintainer can tune without
breaking the contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent / "secondbrain"
AGENT_DEF = PLUGIN_ROOT / "agents" / "secondbrain-morning-brief.md"


@pytest.fixture(scope="module")
def agent_text() -> str:
    assert AGENT_DEF.is_file(), (
        f"secondbrain-morning-brief subagent definition must exist at "
        f"{AGENT_DEF}; create it during T14."
    )
    return AGENT_DEF.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def frontmatter(agent_text: str) -> str:
    assert agent_text.startswith("---\n"), (
        "secondbrain-morning-brief.md must start with a '---' frontmatter "
        "block; Claude Code subagent definitions require one."
    )
    end = agent_text.find("\n---", 4)
    assert end > 0, "frontmatter block is unterminated"
    return agent_text[4:end]


@pytest.fixture(scope="module")
def body(agent_text: str) -> str:
    assert agent_text.startswith("---\n")
    end = agent_text.find("\n---", 4)
    assert end > 0
    return agent_text[end + 4 :]


class TestFilePresence:
    def test_file_exists(self):
        assert AGENT_DEF.is_file(), (
            f"secondbrain-morning-brief subagent definition missing at "
            f"{AGENT_DEF}"
        )

    def test_file_is_markdown(self):
        assert AGENT_DEF.suffix == ".md"

    def test_file_not_empty(self, agent_text: str):
        assert len(agent_text) > 500, (
            f"secondbrain-morning-brief.md is suspiciously short "
            f"({len(agent_text)} bytes); the spec requires inputs, "
            f"write rules, output format, and forbidden actions."
        )


class TestFrontmatterFields:
    def test_frontmatter_has_name(self, frontmatter: str):
        assert "name:" in frontmatter
        assert "secondbrain-morning-brief" in frontmatter, (
            "frontmatter name must be 'secondbrain-morning-brief' — the "
            "scheduled task dispatches via `claude --agent "
            "secondbrain-morning-brief` or Task."
        )

    def test_frontmatter_has_description(self, frontmatter: str):
        assert "description:" in frontmatter

    def test_frontmatter_description_mentions_morning_brief(self, frontmatter: str):
        low = frontmatter.lower()
        assert "morning" in low, (
            "description should identify this as the morning-brief "
            "subagent so `claude --agent` listings are self-explanatory."
        )

    def test_frontmatter_has_tools_allowlist(self, frontmatter: str):
        assert "tools:" in frontmatter, (
            "frontmatter must declare a 'tools:' list — subagent tool "
            "allowlist. Claude Code will otherwise grant broad access."
        )

    def test_tools_allowlist_has_read(self, frontmatter: str):
        assert "Read" in frontmatter

    def test_tools_allowlist_has_bash(self, frontmatter: str):
        assert "Bash" in frontmatter

    def test_tools_allowlist_has_mcp_vault_read(self, frontmatter: str):
        assert "mcp__obsidian__vault_read" in frontmatter

    def test_tools_allowlist_has_mcp_vault_list(self, frontmatter: str):
        assert "mcp__obsidian__vault_list" in frontmatter

    def test_tools_allowlist_has_mcp_vault_update(self, frontmatter: str):
        # The subagent writes brain/morning-brief.md — vault_update is
        # the atomic overwrite path.
        assert "mcp__obsidian__vault_update" in frontmatter

    def test_tools_allowlist_has_mcp_vault_patch(self, frontmatter: str):
        # log.md entries are appended via vault_patch.
        assert "mcp__obsidian__vault_patch" in frontmatter

    def test_tools_allowlist_has_mcp_vault_search(self, frontmatter: str):
        assert "mcp__obsidian__vault_search" in frontmatter

    def test_frontmatter_has_disallowed_tools(self, frontmatter: str):
        assert "disallowedTools:" in frontmatter, (
            "frontmatter must declare 'disallowedTools:' — explicit "
            "denylist of filesystem writes and subagent recursion."
        )

    def test_disallowed_tools_includes_edit(self, frontmatter: str):
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
        """Subagents can't spawn subagents. Explicitly block Task so a
        prompt-injection attempt can't recruit the morning-brief runner
        to fan out into other subagents (per Claude Code docs: nested
        subagent dispatch is a known foot-gun).
        """
        idx = frontmatter.find("disallowedTools:")
        assert idx >= 0
        tail = frontmatter[idx:]
        assert "Task" in tail

    def test_disallowed_tools_includes_web_fetch(self, frontmatter: str):
        idx = frontmatter.find("disallowedTools:")
        assert idx >= 0
        tail = frontmatter[idx:]
        assert "WebFetch" in tail

    def test_disallowed_tools_includes_web_search(self, frontmatter: str):
        idx = frontmatter.find("disallowedTools:")
        assert idx >= 0
        tail = frontmatter[idx:]
        assert "WebSearch" in tail


class TestBodyOutputTarget:
    def test_body_mentions_morning_brief_file(self, body: str):
        assert "brain/morning-brief.md" in body, (
            "body must name the output file `brain/morning-brief.md` — "
            "that's the cached brief the user reads on demand."
        )

    def test_body_mentions_atomic_write(self, body: str):
        """The brief is overwritten as one unit so readers never see a
        half-written file. vault_update is the atomic rewrite path.
        """
        low = body.lower()
        assert "vault_update" in body or "atomic" in low or "overwrite" in low, (
            "body should describe the brief as an atomic/single-shot "
            "write — it's a cached snapshot, not an incremental file."
        )


class TestBodyInputScan:
    def test_body_mentions_inbox(self, body: str):
        assert "inbox" in body.lower(), (
            "body must describe scanning the inbox — surfacing unprocessed "
            "items is part of the morning brief contract."
        )

    def test_body_mentions_deadlines(self, body: str):
        assert "deadline" in body.lower()

    def test_body_mentions_status(self, body: str):
        assert "status" in body.lower()

    def test_body_mentions_urgent(self, body: str):
        """Urgent/today items are the main reason the brief exists —
        without this, it's just a file listing.
        """
        low = body.lower()
        assert "urgent" in low or "today" in low or "priority" in low


class TestBodyLogging:
    def test_body_mentions_log_md(self, body: str):
        """The subagent must append a brief entry to log.md so the user
        can tell when the last morning brief ran.
        """
        assert "log.md" in body


class TestBodyForbiddenActions:
    def test_body_has_forbidden_section(self, body: str):
        assert "Forbidden" in body or "forbidden" in body

    def test_body_forbids_user_interaction(self, body: str):
        """The subagent is a scheduled job — it must not prompt the user.
        The user reads the cached file during whats-next, not in a live
        conversation with the morning-brief runner.
        """
        low = body.lower()
        assert "user" in low
        assert (
            "no user" in low
            or "never talk" in low
            or "not talk" in low
            or "no prompt" in low
            or "do not prompt" in low
            or "silent" in low
            or "no interaction" in low
            or "no conversation" in low
        ), (
            "body must forbid user interaction — the morning brief "
            "subagent runs unattended at 08:00 and its output is a file, "
            "not a conversation."
        )

    def test_body_forbids_hot_memory_direct_write(self, body: str):
        """Hot-memory is owned by the ingester (incremental) and dream-
        protocol (full regenerate). The morning-brief subagent must not
        poke hot-memory.md directly — it writes morning-brief.md, which
        is a separate file.
        """
        low = body.lower()
        assert "hot-memory" in low or "hot memory" in low, (
            "body must warn against touching hot-memory — this is a "
            "common confusion since both are 'cached context files'."
        )
