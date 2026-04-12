"""Integration tests for connect-mcp MCP server.

These tests require Obsidian to be running with the connect-mcp plugin active.
They test the actual MCP protocol handshake and tool calls end-to-end.

Skip with: pytest -m "not integration"
Run only: pytest -m integration
"""

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

import pytest

# Config from environment or defaults
MCP_PORT = int(os.environ.get("OBSIDIAN_MCP_PORT", "27124"))
MCP_KEY = os.environ.get("OBSIDIAN_API_KEY", "")
MCP_BASE = f"http://localhost:{MCP_PORT}"


def mcp_available() -> bool:
    """Check if the MCP server is reachable."""
    try:
        req = urllib.request.Request(f"{MCP_BASE}/health")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
            return data.get("status") == "ok"
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return False


def read_api_key() -> str:
    """Try to read API key from connect-mcp config or env var."""
    if MCP_KEY:
        return MCP_KEY
    # Try common vault locations
    for vault_hint in [os.environ.get("VAULT_PATH", ""), str(Path.home() / "cowork")]:
        config = Path(vault_hint) / ".obsidian" / "plugins" / "connect-mcp" / "data.json"
        if config.exists():
            try:
                data = json.loads(config.read_text())
                key = data.get("apiKey") or data.get("api_key", "")
                if key:
                    return key
            except (json.JSONDecodeError, OSError):
                pass
    return ""


skip_no_mcp = pytest.mark.skipif(
    not mcp_available(),
    reason="Obsidian MCP server not running (need Obsidian with connect-mcp plugin)",
)

API_KEY = read_api_key()
skip_no_key = pytest.mark.skipif(not API_KEY, reason="No API key found for connect-mcp")

pytestmark = [pytest.mark.integration, skip_no_mcp, skip_no_key]


class MCPSession:
    """Manages an MCP session lifecycle."""

    def __init__(self):
        self.session_id = None
        self.msg_counter = 0
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {API_KEY}",
        }

    def call(self, method: str, params: dict = None) -> dict:
        self.msg_counter += 1
        body: dict = {"jsonrpc": "2.0", "id": self.msg_counter, "method": method}
        if params:
            body["params"] = params

        req = urllib.request.Request(
            f"{MCP_BASE}/mcp", json.dumps(body).encode(), self.headers,
        )
        if self.session_id:
            req.add_header("Mcp-Session-Id", self.session_id)

        with urllib.request.urlopen(req, timeout=15) as resp:
            if not self.session_id:
                self.session_id = resp.headers.get("Mcp-Session-Id")
            raw = resp.read().decode()
            for line in raw.split("\n"):
                if line.startswith("data:"):
                    return json.loads(line[5:])
        return {}

    def notify(self, method: str):
        body = {"jsonrpc": "2.0", "method": method}
        req = urllib.request.Request(
            f"{MCP_BASE}/mcp", json.dumps(body).encode(), self.headers,
        )
        if self.session_id:
            req.add_header("Mcp-Session-Id", self.session_id)
        urllib.request.urlopen(req, timeout=5)

    def tool_call(self, name: str, arguments: dict = None) -> str:
        """Call a tool and return the text content."""
        result = self.call("tools/call", {"name": name, "arguments": arguments or {}})
        return result.get("result", {}).get("content", [{}])[0].get("text", "")

    def initialize(self):
        result = self.call("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "pytest-integration", "version": "1.0"},
        })
        self.notify("notifications/initialized")
        return result


@pytest.fixture
def mcp():
    session = MCPSession()
    session.initialize()
    return session


class TestMCPHandshake:
    def test_health_check(self):
        req = urllib.request.Request(f"{MCP_BASE}/health")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        assert data["status"] == "ok"
        assert data["plugin"] == "obsidian-connect-mcp"

    def test_initialize(self):
        session = MCPSession()
        result = session.initialize()
        assert result["result"]["serverInfo"]["name"] == "obsidian-connect-mcp"
        assert session.session_id is not None

    def test_tools_list(self, mcp: MCPSession):
        result = mcp.call("tools/list", {})
        tools = {t["name"] for t in result["result"]["tools"]}
        # Verify all expected tools exist
        expected = {
            "vault_list", "vault_read", "vault_create", "vault_update",
            "vault_delete", "vault_search", "vault_edit", "vault_edit_line",
            "vault_patch", "graph_info", "graph_links", "dataview_query",
        }
        assert expected.issubset(tools), f"Missing tools: {expected - tools}"


class TestVaultOperations:
    def test_vault_list(self, mcp: MCPSession):
        text = mcp.tool_call("vault_list", {"path": "brain"})
        assert len(text) > 0
        data = json.loads(text)
        assert data["total"] > 0 or len(data["files"]) > 0

    def test_vault_read(self, mcp: MCPSession):
        text = mcp.tool_call("vault_read", {"path": "_MANIFEST.md"})
        assert "Manifest" in text or "manifest" in text.lower()

    def test_vault_search(self, mcp: MCPSession):
        text = mcp.tool_call("vault_search", {"query": "status"})
        assert len(text) > 0


class TestDataviewQuery:
    def test_list_query(self, mcp: MCPSession):
        text = mcp.tool_call("dataview_query", {"query": 'LIST FROM "entities" LIMIT 3'})
        assert len(text) > 0
        # Should return JSON with items
        data = json.loads(text)
        assert data["type"] == "list"
        assert len(data["items"]) > 0

    def test_task_query(self, mcp: MCPSession):
        text = mcp.tool_call("dataview_query", {"query": 'TASK FROM "brain/status" LIMIT 3'})
        assert len(text) > 0


class TestGraphOperations:
    def test_graph_links(self, mcp: MCPSession):
        text = mcp.tool_call("graph_links", {"path": "brain/status.md"})
        assert len(text) > 0
        # status.md should have many links
        assert text.count("\n") > 5

    def test_graph_info(self, mcp: MCPSession):
        text = mcp.tool_call("graph_info", {"path": "brain/status.md"})
        assert len(text) > 0


class TestWriteOperations:
    """Test write operations using a scratch file that we clean up."""

    SCRATCH_PATH = "scratch/_mcp_test_temp.md"

    def test_create_read_delete(self, mcp: MCPSession):
        """Full lifecycle: create, read, verify, delete."""
        # Create
        create_result = mcp.tool_call("vault_create", {
            "path": self.SCRATCH_PATH,
            "content": "# MCP Test\n\nThis is a test file created by pytest.\n",
        })
        assert "created" in create_result.lower() or "success" in create_result.lower() or len(create_result) > 0

        # Read back
        text = mcp.tool_call("vault_read", {"path": self.SCRATCH_PATH})
        assert "MCP Test" in text

        # Delete
        delete_result = mcp.tool_call("vault_delete", {"path": self.SCRATCH_PATH})
        assert len(delete_result) > 0
