"""Tests for connect_mcp_client.py — HTTP wrapper for Connect MCP.

This wrapper gives plugin-internal Python scripts (dream-protocol, hot-memory
updater, doctor treatment phase, etc.) a clean way to talk to the Connect MCP
server without shelling out to `curl` or going through the Claude tool layer.

All HTTP is mocked via `unittest.mock.patch` on `urllib.request.urlopen` — no
real network I/O runs from this test file.

Shape of the real Connect MCP server (StreamableHTTPServerTransport from the
MCP TypeScript SDK): requests go to POST /mcp with an Accept header of
`application/json, text/event-stream`. The server responds with an SSE stream
whose `data:` lines carry the JSON-RPC response. The first call must be
`initialize`, which returns a session ID in the `Mcp-Session-Id` response
header; subsequent calls echo that header back so the server routes them to
the right session. We emulate all of that through a fake urlopen.
"""

from __future__ import annotations

import io
import json
import sys
import urllib.error
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "secondbrain" / "scripts"))

from connect_mcp_client import (  # type: ignore[reportMissingImports]
    ConnectMCPAuthFailed,
    ConnectMCPClient,
    ConnectMCPError,
    ConnectMCPNotFound,
    ConnectMCPRequestFailed,
    ConnectMCPUnreachable,
)


# ---------------------------------------------------------------------------
# Fake-response helpers
# ---------------------------------------------------------------------------

class FakeResponse:
    """Minimal stand-in for the object returned by urlopen().__enter__().

    The real Connect MCP server returns text/event-stream with a body like:

        event: message
        data: {"jsonrpc":"2.0","id":1,"result":{...}}

    Tests pass in a `body` string and an optional session_id header.
    """

    def __init__(
        self,
        body: str,
        status: int = 200,
        session_id: Optional[str] = "test-session-id",
        content_type: str = "text/event-stream",
    ):
        self._body = body.encode("utf-8") if isinstance(body, str) else body
        self.status = status
        self.headers = {
            "Content-Type": content_type,
            "Mcp-Session-Id": session_id or "",
        }

    def read(self) -> bytes:
        return self._body

    def getcode(self) -> int:
        return self.status

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        del exc_type, exc_val, exc_tb
        return None


def _sse(payload: Dict[str, Any]) -> str:
    """Wrap a JSON-RPC payload in a minimal SSE frame."""
    return f"event: message\ndata: {json.dumps(payload)}\n\n"


def _ok_initialize_response(request_id: int = 1) -> str:
    return _sse({
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "protocolVersion": "2025-03-26",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "obsidian-connect-mcp", "version": "1.0.0"},
        },
    })


def _ok_tool_response(text: str, request_id: int = 2) -> str:
    return _sse({
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "content": [{"type": "text", "text": text}],
        },
    })


def _error_response(code: int, message: str, request_id: int = 2) -> str:
    return _sse({
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    })


class FakeUrlopen:
    """Callable stand-in for urllib.request.urlopen.

    Returns responses from a FIFO queue. Each call captures the Request object
    so the test can assert the URL, headers, and body after the fact.
    """

    def __init__(self, responses: List[FakeResponse]):
        self._responses = list(responses)
        self.calls: List[Dict[str, Any]] = []

    def __call__(self, req: Any, timeout: Optional[float] = None) -> FakeResponse:
        # Capture the call for later assertions.
        body_bytes = req.data or b""
        body_str = body_bytes.decode("utf-8") if isinstance(body_bytes, (bytes, bytearray)) else ""
        try:
            body_json = json.loads(body_str) if body_str else None
        except json.JSONDecodeError:
            body_json = None
        self.calls.append({
            "url": req.full_url,
            "method": req.get_method(),
            "headers": dict(req.header_items()),
            "body": body_str,
            "body_json": body_json,
            "timeout": timeout,
        })
        if not self._responses:
            raise AssertionError("FakeUrlopen ran out of queued responses")
        return self._responses.pop(0)


def _queue_init_plus(*tool_responses: FakeResponse) -> FakeUrlopen:
    """Build a FakeUrlopen that answers `initialize`, the `notifications/initialized`
    POST, and then the supplied tool-call responses in order.

    The notifications/initialized POST is a JSON-RPC notification — the server
    returns an empty 202 body. We emulate that with an empty SSE response.
    """
    init = FakeResponse(_ok_initialize_response(), session_id="sess-1")
    # notifications/initialized is a notification — no response body needed, but
    # urlopen will still return something, so we return an empty body with 202.
    ack = FakeResponse("", status=202, session_id="sess-1")
    return FakeUrlopen([init, ack, *tool_responses])


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def env_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set the two required env vars to known values."""
    monkeypatch.setenv("OBSIDIAN_MCP_PORT", "27124")
    monkeypatch.setenv("OBSIDIAN_API_KEY", "test-key-abc")


@pytest.fixture
def env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure the env vars are not set."""
    monkeypatch.delenv("OBSIDIAN_MCP_PORT", raising=False)
    monkeypatch.delenv("OBSIDIAN_API_KEY", raising=False)


# ---------------------------------------------------------------------------
# Construction + env handling
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("env_set")
class TestConstruction:
    def test_env_vars_set_succeeds(self):
        client = ConnectMCPClient()
        # Accessing internal-but-stable attributes to validate config.
        assert client.host == "localhost"
        assert client.port == 27124
        assert client.api_key == "test-key-abc"
        assert client.timeout == 10.0

    def test_custom_host_and_timeout(self):
        client = ConnectMCPClient(host="127.0.0.1", timeout=5.5)
        assert client.host == "127.0.0.1"
        assert client.timeout == 5.5

    def test_explicit_port_overrides_env(self):
        client = ConnectMCPClient(port=9999)
        assert client.port == 9999
        assert client.api_key == "test-key-abc"

    def test_explicit_key_overrides_env(self):
        client = ConnectMCPClient(api_key="override-key")
        assert client.api_key == "override-key"
        assert client.port == 27124


@pytest.mark.usefixtures("env_unset")
class TestConstructionMissingEnv:
    def test_missing_env_vars_raises(self):
        with pytest.raises(ConnectMCPUnreachable) as excinfo:
            ConnectMCPClient()
        assert "OBSIDIAN_MCP_PORT" in str(excinfo.value) or "OBSIDIAN_API_KEY" in str(excinfo.value)

    def test_missing_port_raises_even_with_key(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OBSIDIAN_API_KEY", "some-key")
        with pytest.raises(ConnectMCPUnreachable):
            ConnectMCPClient()

    def test_missing_key_raises_even_with_port(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OBSIDIAN_MCP_PORT", "27124")
        with pytest.raises(ConnectMCPUnreachable):
            ConnectMCPClient()

    def test_explicit_overrides_allow_construction_without_env(self):
        # Neither env var is set, but we pass explicit values — should work.
        client = ConnectMCPClient(port=12345, api_key="explicit-key")
        assert client.port == 12345
        assert client.api_key == "explicit-key"


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------

class TestExceptionHierarchy:
    def test_all_subclass_connect_mcp_error(self):
        assert issubclass(ConnectMCPUnreachable, ConnectMCPError)
        assert issubclass(ConnectMCPAuthFailed, ConnectMCPError)
        assert issubclass(ConnectMCPNotFound, ConnectMCPError)
        assert issubclass(ConnectMCPRequestFailed, ConnectMCPError)

    def test_connect_mcp_error_is_exception(self):
        assert issubclass(ConnectMCPError, Exception)


# ---------------------------------------------------------------------------
# Request body shape + headers
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("env_set")
class TestRequestShape:
    def test_vault_read_posts_correct_body(self):
        fake = _queue_init_plus(
            FakeResponse(_ok_tool_response(json.dumps({
                "path": "brain/status.md",
                "content": "# Status\n\nHello",
                "frontmatter": None,
            }))),
        )
        with patch("urllib.request.urlopen", side_effect=fake):
            client = ConnectMCPClient()
            result = client.vault_read("brain/status.md")

        # Expected call sequence: initialize, notifications/initialized, tools/call
        assert len(fake.calls) == 3
        init_call, ack_call, tool_call = fake.calls

        # Every call hits POST /mcp on localhost:27124
        for call in fake.calls:
            assert call["url"] == "http://localhost:27124/mcp"
            assert call["method"] == "POST"

        # initialize call
        assert init_call["body_json"]["method"] == "initialize"
        assert init_call["body_json"]["jsonrpc"] == "2.0"
        assert "id" in init_call["body_json"]

        # notifications/initialized has no id (it's a notification)
        assert ack_call["body_json"]["method"] == "notifications/initialized"
        assert "id" not in ack_call["body_json"]

        # tools/call
        assert tool_call["body_json"]["method"] == "tools/call"
        assert tool_call["body_json"]["params"]["name"] == "vault_read"
        assert tool_call["body_json"]["params"]["arguments"] == {"path": "brain/status.md"}

        # The vault_read helper parses the wrapped JSON and returns the content string.
        assert result == "# Status\n\nHello"

    def test_auth_header_is_bearer(self):
        fake = _queue_init_plus(FakeResponse(_ok_tool_response("ok")))
        with patch("urllib.request.urlopen", side_effect=fake):
            client = ConnectMCPClient()
            client.call_tool("vault_list", {"path": "/"})

        for call in fake.calls:
            # Header names come back title-cased from urllib's Request.header_items().
            header_lookup = {k.lower(): v for k, v in call["headers"].items()}
            assert header_lookup.get("authorization") == "Bearer test-key-abc"

    def test_content_type_is_json(self):
        fake = _queue_init_plus(FakeResponse(_ok_tool_response("ok")))
        with patch("urllib.request.urlopen", side_effect=fake):
            client = ConnectMCPClient()
            client.call_tool("vault_list", {"path": "/"})

        tool_call = fake.calls[-1]
        header_lookup = {k.lower(): v for k, v in tool_call["headers"].items()}
        assert header_lookup.get("content-type") == "application/json"

    def test_accept_header_allows_sse(self):
        """StreamableHTTPServerTransport requires Accept to include text/event-stream."""
        fake = _queue_init_plus(FakeResponse(_ok_tool_response("ok")))
        with patch("urllib.request.urlopen", side_effect=fake):
            client = ConnectMCPClient()
            client.call_tool("vault_list", {"path": "/"})

        for call in fake.calls:
            header_lookup = {k.lower(): v for k, v in call["headers"].items()}
            accept = header_lookup.get("accept", "")
            assert "text/event-stream" in accept
            assert "application/json" in accept

    def test_session_id_echoed_on_subsequent_requests(self):
        """After initialize returns Mcp-Session-Id, subsequent calls must echo it."""
        fake = _queue_init_plus(FakeResponse(_ok_tool_response("ok")))
        with patch("urllib.request.urlopen", side_effect=fake):
            client = ConnectMCPClient()
            client.call_tool("vault_list", {"path": "/"})

        # init call should NOT carry a session ID (it's creating one)
        init_headers = {k.lower(): v for k, v in fake.calls[0]["headers"].items()}
        assert "mcp-session-id" not in init_headers

        # All subsequent calls (ack + tool) should carry the session ID.
        for call in fake.calls[1:]:
            header_lookup = {k.lower(): v for k, v in call["headers"].items()}
            assert header_lookup.get("mcp-session-id") == "sess-1"

    def test_request_id_is_unique_per_request(self):
        fake = _queue_init_plus(
            FakeResponse(_ok_tool_response("a")),
            FakeResponse(_ok_tool_response("b")),
        )
        with patch("urllib.request.urlopen", side_effect=fake):
            client = ConnectMCPClient()
            client.call_tool("vault_list", {"path": "/"})
            client.call_tool("vault_list", {"path": "/"})

        # Collect all request IDs across non-notification calls.
        ids = []
        for call in fake.calls:
            body = call["body_json"]
            if body and "id" in body:
                ids.append(body["id"])

        assert len(ids) == len(set(ids)), f"duplicate request IDs: {ids}"

    def test_host_override_used_in_url(self):
        fake = _queue_init_plus(FakeResponse(_ok_tool_response("ok")))
        with patch("urllib.request.urlopen", side_effect=fake):
            client = ConnectMCPClient(host="example.test")
            client.call_tool("vault_list", {"path": "/"})

        for call in fake.calls:
            assert call["url"] == "http://example.test:27124/mcp"


# ---------------------------------------------------------------------------
# Response parsing — happy paths
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("env_set")
class TestResponseParsingHappy:
    def test_vault_read_extracts_content_from_json_wrapper(self):
        fake = _queue_init_plus(
            FakeResponse(_ok_tool_response(json.dumps({
                "path": "brain/status.md",
                "content": "file contents here",
                "frontmatter": {"updated": "2026-04-10"},
            }))),
        )
        with patch("urllib.request.urlopen", side_effect=fake):
            client = ConnectMCPClient()
            assert client.vault_read("brain/status.md") == "file contents here"

    def test_vault_read_falls_back_to_raw_text_when_not_json(self):
        """If the server returns a bare text body (not the JSON wrapper), return
        it verbatim — some fallback servers or errors may do this."""
        fake = _queue_init_plus(
            FakeResponse(_ok_tool_response("literal file contents")),
        )
        with patch("urllib.request.urlopen", side_effect=fake):
            client = ConnectMCPClient()
            assert client.vault_read("brain/status.md") == "literal file contents"

    def test_vault_list_returns_list_of_paths(self):
        payload = {
            "folders": ["brain", "entities"],
            "files": ["brain/status.md", "brain/deadlines.md"],
            "total": 2,
        }
        fake = _queue_init_plus(
            FakeResponse(_ok_tool_response(json.dumps(payload))),
        )
        with patch("urllib.request.urlopen", side_effect=fake):
            client = ConnectMCPClient()
            result = client.vault_list("brain")

        assert isinstance(result, list)
        assert "brain/status.md" in result
        assert "brain/deadlines.md" in result

    def test_vault_create_returns_dict(self):
        fake = _queue_init_plus(
            FakeResponse(_ok_tool_response(json.dumps({"created": "scratch/new.md"}))),
        )
        with patch("urllib.request.urlopen", side_effect=fake):
            client = ConnectMCPClient()
            result = client.vault_create("scratch/new.md", "# New")
        assert isinstance(result, dict)
        assert result.get("created") == "scratch/new.md"

    def test_vault_search_returns_list_of_results(self):
        payload = {
            "query": "status",
            "results": [
                {"path": "brain/status.md", "matches": ["1: # Status"]},
                {"path": "log.md", "matches": ["3: session-start"]},
            ],
            "totalMatches": 2,
            "shown": 2,
        }
        fake = _queue_init_plus(
            FakeResponse(_ok_tool_response(json.dumps(payload))),
        )
        with patch("urllib.request.urlopen", side_effect=fake):
            client = ConnectMCPClient()
            result = client.vault_search("status")

        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["path"] == "brain/status.md"

    def test_dataview_query_returns_list_of_items(self):
        payload = {
            "type": "list",
            "items": [{"path": "entities/alice.md"}, {"path": "entities/bob.md"}],
        }
        fake = _queue_init_plus(
            FakeResponse(_ok_tool_response(json.dumps(payload))),
        )
        with patch("urllib.request.urlopen", side_effect=fake):
            client = ConnectMCPClient()
            result = client.dataview_query('LIST FROM "entities"')

        assert isinstance(result, list)
        assert len(result) == 2

    def test_active_note_returns_dict(self):
        payload = {"active": True, "path": "brain/status.md", "content": "..."}
        fake = _queue_init_plus(
            FakeResponse(_ok_tool_response(json.dumps(payload))),
        )
        with patch("urllib.request.urlopen", side_effect=fake):
            client = ConnectMCPClient()
            result = client.active_note()

        assert isinstance(result, dict)
        assert result["path"] == "brain/status.md"

    def test_graph_info_returns_dict(self):
        payload = {"path": "brain/status.md", "outLinks": 5, "inLinks": 2, "tags": []}
        fake = _queue_init_plus(
            FakeResponse(_ok_tool_response(json.dumps(payload))),
        )
        with patch("urllib.request.urlopen", side_effect=fake):
            client = ConnectMCPClient()
            result = client.graph_info("brain/status.md")

        assert isinstance(result, dict)
        assert result["outLinks"] == 5

    def test_call_tool_returns_raw_dict(self):
        """The generic escape hatch should return the parsed content dict as-is."""
        payload = {"arbitrary": "shape", "count": 42}
        fake = _queue_init_plus(
            FakeResponse(_ok_tool_response(json.dumps(payload))),
        )
        with patch("urllib.request.urlopen", side_effect=fake):
            client = ConnectMCPClient()
            result = client.call_tool("some_future_tool", {"arg": "value"})

        assert isinstance(result, dict)
        assert result["count"] == 42


# ---------------------------------------------------------------------------
# Response parsing — error paths
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("env_set")
class TestErrorMapping:
    def test_mcp_error_response_raises_request_failed(self):
        fake = _queue_init_plus(
            FakeResponse(_error_response(-32000, "Session expired")),
        )
        with patch("urllib.request.urlopen", side_effect=fake):
            client = ConnectMCPClient()
            with pytest.raises(ConnectMCPRequestFailed) as excinfo:
                client.vault_read("brain/status.md")
        assert "Session expired" in str(excinfo.value)

    def test_mcp_method_not_found_raises_not_found(self):
        fake = _queue_init_plus(
            FakeResponse(_error_response(-32601, "Method not found")),
        )
        with patch("urllib.request.urlopen", side_effect=fake):
            client = ConnectMCPClient()
            with pytest.raises(ConnectMCPNotFound):
                client.call_tool("nonexistent_tool", {})

    def test_mcp_unknown_error_code_raises_request_failed(self):
        fake = _queue_init_plus(
            FakeResponse(_error_response(-99999, "weird error")),
        )
        with patch("urllib.request.urlopen", side_effect=fake):
            client = ConnectMCPClient()
            with pytest.raises(ConnectMCPRequestFailed) as excinfo:
                client.vault_read("brain/status.md")
        assert "weird error" in str(excinfo.value)

    def test_http_401_raises_auth_failed(self):
        # The init handshake itself gets back a 401.
        error = urllib.error.HTTPError(
            url="http://localhost:27124/mcp", code=401, msg="Unauthorized",
            hdrs=None,  # type: ignore[arg-type]
            fp=io.BytesIO(b'{"error": "Unauthorized"}'),
        )
        with patch("urllib.request.urlopen", side_effect=error):
            client = ConnectMCPClient()
            with pytest.raises(ConnectMCPAuthFailed):
                client.vault_read("brain/status.md")

    def test_http_403_raises_auth_failed(self):
        error = urllib.error.HTTPError(
            url="http://localhost:27124/mcp", code=403, msg="Forbidden",
            hdrs=None,  # type: ignore[arg-type]
            fp=io.BytesIO(b'{"error": "Forbidden"}'),
        )
        with patch("urllib.request.urlopen", side_effect=error):
            client = ConnectMCPClient()
            with pytest.raises(ConnectMCPAuthFailed):
                client.vault_read("brain/status.md")

    def test_http_404_raises_not_found(self):
        error = urllib.error.HTTPError(
            url="http://localhost:27124/mcp", code=404, msg="Not Found",
            hdrs=None,  # type: ignore[arg-type]
            fp=io.BytesIO(b'{"error": "Not Found"}'),
        )
        with patch("urllib.request.urlopen", side_effect=error):
            client = ConnectMCPClient()
            with pytest.raises(ConnectMCPNotFound):
                client.vault_read("missing.md")

    def test_http_500_raises_request_failed(self):
        error = urllib.error.HTTPError(
            url="http://localhost:27124/mcp", code=500, msg="Server Error",
            hdrs=None,  # type: ignore[arg-type]
            fp=io.BytesIO(b'{"error": "oops"}'),
        )
        with patch("urllib.request.urlopen", side_effect=error):
            client = ConnectMCPClient()
            with pytest.raises(ConnectMCPRequestFailed):
                client.vault_read("brain/status.md")

    def test_connection_refused_raises_unreachable(self):
        error = urllib.error.URLError(ConnectionRefusedError("Connection refused"))
        with patch("urllib.request.urlopen", side_effect=error):
            client = ConnectMCPClient()
            with pytest.raises(ConnectMCPUnreachable):
                client.vault_read("brain/status.md")

    def test_timeout_raises_unreachable(self):
        error = urllib.error.URLError("timed out")
        with patch("urllib.request.urlopen", side_effect=error):
            client = ConnectMCPClient()
            with pytest.raises(ConnectMCPUnreachable):
                client.vault_read("brain/status.md")

    def test_generic_oserror_raises_unreachable(self):
        """Socket-level OSError (e.g. DNS failure bubbled up) → unreachable."""
        error = OSError("network unreachable")
        with patch("urllib.request.urlopen", side_effect=error):
            client = ConnectMCPClient()
            with pytest.raises(ConnectMCPUnreachable):
                client.vault_read("brain/status.md")

    def test_malformed_json_raises_request_failed(self):
        # Return SSE-framed data that isn't valid JSON.
        malformed = "event: message\ndata: {not valid json\n\n"
        # We can't use _queue_init_plus because the bad response is on the init
        # call itself; an init failure should still map to RequestFailed.
        fake = FakeUrlopen([FakeResponse(malformed)])
        with patch("urllib.request.urlopen", side_effect=fake):
            client = ConnectMCPClient()
            with pytest.raises(ConnectMCPRequestFailed):
                client.vault_read("brain/status.md")

    def test_empty_body_raises_request_failed(self):
        fake = FakeUrlopen([FakeResponse("")])
        with patch("urllib.request.urlopen", side_effect=fake):
            client = ConnectMCPClient()
            with pytest.raises(ConnectMCPRequestFailed):
                client.vault_read("brain/status.md")


# ---------------------------------------------------------------------------
# is_reachable — must never raise
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("env_set")
class TestIsReachable:
    def test_returns_true_when_vault_list_succeeds(self):
        # vault_list returns a list payload wrapped as JSON text.
        payload = {"folders": [], "files": [], "total": 0}
        fake = _queue_init_plus(
            FakeResponse(_ok_tool_response(json.dumps(payload))),
        )
        with patch("urllib.request.urlopen", side_effect=fake):
            client = ConnectMCPClient()
            assert client.is_reachable() is True

    def test_returns_false_on_connection_refused(self):
        error = urllib.error.URLError(ConnectionRefusedError("refused"))
        with patch("urllib.request.urlopen", side_effect=error):
            client = ConnectMCPClient()
            assert client.is_reachable() is False

    def test_returns_false_on_auth_failed(self):
        error = urllib.error.HTTPError(
            url="http://localhost:27124/mcp", code=401, msg="Unauthorized",
            hdrs=None,  # type: ignore[arg-type]
            fp=io.BytesIO(b""),
        )
        with patch("urllib.request.urlopen", side_effect=error):
            client = ConnectMCPClient()
            assert client.is_reachable() is False

    def test_returns_false_on_mcp_error(self):
        fake = _queue_init_plus(
            FakeResponse(_error_response(-32000, "boom")),
        )
        with patch("urllib.request.urlopen", side_effect=fake):
            client = ConnectMCPClient()
            assert client.is_reachable() is False

    def test_returns_false_on_unexpected_exception(self):
        error = RuntimeError("something weird")
        with patch("urllib.request.urlopen", side_effect=error):
            client = ConnectMCPClient()
            # is_reachable() must never raise.
            assert client.is_reachable() is False


# ---------------------------------------------------------------------------
# Per-method argument dispatch coverage
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("env_set")
class TestPublicMethodsDispatchArguments:
    """For each wrapper, verify it calls the right tool with the right arg shape.

    This doesn't exhaustively test every tool; it picks representative coverage
    across the categories (core, edit, metadata, dataview) so a regression in
    any category breaks a test.
    """

    def _run(self, response: FakeResponse, action):
        fake = _queue_init_plus(response)
        with patch("urllib.request.urlopen", side_effect=fake):
            client = ConnectMCPClient()
            action(client)
        return fake.calls[-1]["body_json"]["params"]

    def test_vault_list_dispatches(self):
        params = self._run(
            FakeResponse(_ok_tool_response(json.dumps({"files": [], "folders": [], "total": 0}))),
            lambda c: c.vault_list("brain"),
        )
        assert params["name"] == "vault_list"
        assert params["arguments"] == {"path": "brain"}

    def test_vault_read_dispatches(self):
        params = self._run(
            FakeResponse(_ok_tool_response(json.dumps({"path": "x.md", "content": "", "frontmatter": None}))),
            lambda c: c.vault_read("x.md"),
        )
        assert params["name"] == "vault_read"
        assert params["arguments"] == {"path": "x.md"}

    def test_vault_create_dispatches(self):
        params = self._run(
            FakeResponse(_ok_tool_response(json.dumps({"created": "new.md"}))),
            lambda c: c.vault_create("new.md", "# hi"),
        )
        assert params["name"] == "vault_create"
        assert params["arguments"] == {"path": "new.md", "content": "# hi"}

    def test_vault_update_dispatches(self):
        params = self._run(
            FakeResponse(_ok_tool_response(json.dumps({"updated": "x.md"}))),
            lambda c: c.vault_update("x.md", "# new"),
        )
        assert params["name"] == "vault_update"
        assert params["arguments"] == {"path": "x.md", "content": "# new"}

    def test_vault_delete_dispatches(self):
        params = self._run(
            FakeResponse(_ok_tool_response(json.dumps({"deleted": "x.md"}))),
            lambda c: c.vault_delete("x.md"),
        )
        assert params["name"] == "vault_delete"
        assert params["arguments"] == {"path": "x.md"}

    def test_vault_search_dispatches(self):
        params = self._run(
            FakeResponse(_ok_tool_response(json.dumps({
                "query": "foo", "results": [], "totalMatches": 0, "shown": 0,
            }))),
            lambda c: c.vault_search("foo"),
        )
        assert params["name"] == "vault_search"
        assert params["arguments"]["query"] == "foo"

    def test_vault_edit_dispatches_with_connect_mcp_arg_names(self):
        """Connect MCP uses oldText/newText, not find/replace."""
        params = self._run(
            FakeResponse(_ok_tool_response(json.dumps({"replaced": True}))),
            lambda c: c.vault_edit("x.md", "old", "new"),
        )
        assert params["name"] == "vault_edit"
        args = params["arguments"]
        assert args["path"] == "x.md"
        assert args["oldText"] == "old"
        assert args["newText"] == "new"

    def test_vault_edit_line_dispatches_with_line_number(self):
        """Connect MCP uses lineNumber (camelCase), not line."""
        params = self._run(
            FakeResponse(_ok_tool_response(json.dumps({"lineNumber": 5}))),
            lambda c: c.vault_edit_line("x.md", 5, "replacement"),
        )
        assert params["name"] == "vault_edit_line"
        args = params["arguments"]
        assert args["path"] == "x.md"
        assert args["lineNumber"] == 5
        assert args["content"] == "replacement"

    def test_vault_patch_dispatches(self):
        params = self._run(
            FakeResponse(_ok_tool_response(json.dumps({"success": True}))),
            lambda c: c.vault_patch("x.md", "heading", "Section", "body", operation="append"),
        )
        assert params["name"] == "vault_patch"
        args = params["arguments"]
        assert args["path"] == "x.md"
        assert args["targetType"] == "heading"
        assert args["target"] == "Section"
        assert args["content"] == "body"
        assert args["operation"] == "append"

    def test_dataview_query_dispatches(self):
        params = self._run(
            FakeResponse(_ok_tool_response(json.dumps({"type": "list", "items": []}))),
            lambda c: c.dataview_query("LIST"),
        )
        assert params["name"] == "dataview_query"
        assert params["arguments"]["query"] == "LIST"

    def test_active_note_dispatches_with_no_args(self):
        params = self._run(
            FakeResponse(_ok_tool_response(json.dumps({"active": False}))),
            lambda c: c.active_note(),
        )
        assert params["name"] == "active_note"
        assert params["arguments"] == {}

    def test_graph_info_dispatches_with_path(self):
        params = self._run(
            FakeResponse(_ok_tool_response(json.dumps({"path": "x.md"}))),
            lambda c: c.graph_info("x.md"),
        )
        assert params["name"] == "graph_info"
        assert params["arguments"] == {"path": "x.md"}

    def test_graph_links_dispatches(self):
        params = self._run(
            FakeResponse(_ok_tool_response(json.dumps({"links": []}))),
            lambda c: c.graph_links("x.md"),
        )
        assert params["name"] == "graph_links"
        assert params["arguments"] == {"path": "x.md"}

    def test_call_tool_dispatches_verbatim(self):
        params = self._run(
            FakeResponse(_ok_tool_response(json.dumps({"anything": True}))),
            lambda c: c.call_tool("custom_tool", {"foo": "bar", "n": 7}),
        )
        assert params["name"] == "custom_tool"
        assert params["arguments"] == {"foo": "bar", "n": 7}


# ---------------------------------------------------------------------------
# Session reuse — initialize should happen at most once per client
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("env_set")
class TestSessionReuse:
    def test_initialize_not_called_twice_for_two_tool_calls(self):
        """Two sequential calls should share one session — no duplicate handshake."""
        fake = _queue_init_plus(
            FakeResponse(_ok_tool_response("first")),
            FakeResponse(_ok_tool_response("second")),
        )
        with patch("urllib.request.urlopen", side_effect=fake):
            client = ConnectMCPClient()
            client.call_tool("vault_list", {})
            client.call_tool("vault_list", {})

        # Expected: 1 init + 1 ack + 2 tool-calls = 4 total.
        assert len(fake.calls) == 4
        methods = [c["body_json"]["method"] for c in fake.calls if c["body_json"]]
        assert methods.count("initialize") == 1
        assert methods.count("notifications/initialized") == 1
        assert methods.count("tools/call") == 2
