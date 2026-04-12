#!/usr/bin/env python3
"""
connect_mcp_client.py — Python HTTP wrapper for the Connect MCP server.

Several plugin-internal scripts (dream-protocol regen, hot-memory updater,
session-start context emitter, doctor treatment phase, etc.) need to talk to
the Connect MCP server from OUTSIDE the Claude agent tool context — they run
from hooks or the CLI, not from an agent session. Without a shared wrapper,
those scripts would have to shell out to `curl` or reimplement JSON-RPC
framing each time. This module centralizes that logic.

The upstream server uses the MCP TypeScript SDK's
`StreamableHTTPServerTransport`, which means:

  - POST requests go to `http://{host}:{port}/mcp`.
  - `Accept` must include both `application/json` and `text/event-stream`.
  - Authorization is a Bearer token from `OBSIDIAN_API_KEY`.
  - The first request must be `initialize`, after which the server returns a
    session ID in the `Mcp-Session-Id` response header. Subsequent requests
    echo that header back to stay on the same session.
  - A `notifications/initialized` notification must be sent after `initialize`
    to finish the handshake.
  - Responses come back SSE-framed: `event: message\\ndata: {json}\\n\\n`.

Stdlib-only, Python 3.8+. No `print()` calls. No side effects at import time.
"""

from __future__ import annotations

import itertools
import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Typed exception hierarchy
# ---------------------------------------------------------------------------

class ConnectMCPError(Exception):
    """Base class for all Connect MCP client errors."""


class ConnectMCPUnreachable(ConnectMCPError):
    """The server could not be contacted at all.

    Raised for connection refused, DNS failures, timeouts, and for
    missing/empty `OBSIDIAN_MCP_PORT`/`OBSIDIAN_API_KEY` environment
    variables at construction time.
    """


class ConnectMCPAuthFailed(ConnectMCPError):
    """The server rejected the request with HTTP 401 or 403.

    Typically means `OBSIDIAN_API_KEY` is wrong or the connect-mcp plugin is
    running with a different key than the one configured in the environment.
    """


class ConnectMCPNotFound(ConnectMCPError):
    """The target was not found.

    Raised for HTTP 404 responses and for JSON-RPC error code -32601
    ("Method not found"). A caller can treat this as "the operation itself
    was reachable but the thing you asked about doesn't exist."
    """


class ConnectMCPRequestFailed(ConnectMCPError):
    """Any other failure: server errors, malformed JSON, non-NotFound MCP
    error responses, or unexpected 5xx HTTP responses."""


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class ConnectMCPClient:
    """Thin Python client for the Connect MCP HTTP server.

    Construction reads `OBSIDIAN_MCP_PORT` and `OBSIDIAN_API_KEY` from the
    environment. Either can be overridden via explicit constructor params
    (useful for tests). If neither env nor explicit value is provided, the
    constructor raises `ConnectMCPUnreachable` — we'd rather fail fast than
    defer the error to the first request.

    A ConnectMCPClient lazily initializes one MCP session on first use and
    reuses it for subsequent calls. Session ID is stored as an instance
    attribute and sent as `Mcp-Session-Id` on every request after init.
    """

    MCP_PROTOCOL_VERSION = "2025-03-26"

    def __init__(
        self,
        port: Optional[int] = None,
        api_key: Optional[str] = None,
        host: str = "localhost",
        timeout: float = 10.0,
    ):
        # Resolve port — explicit wins, else env var, else failure.
        env_port = os.environ.get("OBSIDIAN_MCP_PORT")
        if port is not None:
            self.port: int = int(port)
        elif env_port:
            try:
                self.port = int(env_port)
            except ValueError as exc:
                raise ConnectMCPUnreachable(
                    f"OBSIDIAN_MCP_PORT is not a valid integer: {env_port!r}"
                ) from exc
        else:
            raise ConnectMCPUnreachable(
                "OBSIDIAN_MCP_PORT not set and no explicit port passed to ConnectMCPClient"
            )

        # Resolve API key.
        env_key = os.environ.get("OBSIDIAN_API_KEY")
        if api_key is not None:
            self.api_key: str = api_key
        elif env_key:
            self.api_key = env_key
        else:
            raise ConnectMCPUnreachable(
                "OBSIDIAN_API_KEY not set and no explicit api_key passed to ConnectMCPClient"
            )

        self.host = host
        self.timeout = timeout

        # Session state — populated by `_ensure_initialized()` on first use.
        self._session_id: Optional[str] = None
        self._initialized: bool = False

        # Monotonic request ID counter. Starting at 1 matches MCP convention.
        self._id_counter = itertools.count(1)

    # -----------------------------------------------------------------
    # Public — health
    # -----------------------------------------------------------------

    def is_reachable(self) -> bool:
        """Return True if we can complete a trivial round-trip.

        Never raises — any exception (including unexpected errors) returns
        False. Callers like doctor rely on this to decide whether to attempt
        real work or to surface a "server is down" state.
        """
        try:
            self.vault_list("/")
            return True
        except Exception:  # noqa: BLE001 — intentional: this must never raise
            return False

    # -----------------------------------------------------------------
    # Public — vault core
    # -----------------------------------------------------------------

    def vault_list(self, path: str = "/") -> List[str]:
        """List files under a vault path. Returns a list of relative paths."""
        result = self._call_tool("vault_list", {"path": path})
        # The server returns {"folders": [...], "files": [...], ...}. Flatten
        # to just the file paths since that's what callers typically want.
        if isinstance(result, dict):
            files = result.get("files", [])
            # Files may be strings or dicts with a "path" key (when sort=modified
            # or sort=created). Normalize to strings.
            out: List[str] = []
            for entry in files:
                if isinstance(entry, str):
                    out.append(entry)
                elif isinstance(entry, dict) and "path" in entry:
                    out.append(str(entry["path"]))
            return out
        return []

    def vault_read(self, path: str) -> str:
        """Read the content of a note. Returns the raw file content string.

        The server wraps the response as JSON `{"path", "content", "frontmatter"}`.
        We extract the `content` field so callers get the string they expect.
        If the response is not a JSON wrapper (e.g. a fallback server returning
        bare text), we return the text verbatim.
        """
        result = self._call_tool("vault_read", {"path": path})
        if isinstance(result, dict):
            content = result.get("content")
            if isinstance(content, str):
                return content
            # Unexpected shape — return empty rather than raising, since the
            # call itself succeeded.
            return ""
        # Bare text fallback.
        if isinstance(result, str):
            return result
        return ""

    def vault_create(self, path: str, content: str) -> dict:
        """Create a new note. Returns the server's response dict."""
        result = self._call_tool("vault_create", {"path": path, "content": content})
        return result if isinstance(result, dict) else {"raw": result}

    def vault_update(self, path: str, content: str) -> dict:
        """Replace the entire content of an existing note."""
        result = self._call_tool("vault_update", {"path": path, "content": content})
        return result if isinstance(result, dict) else {"raw": result}

    def vault_delete(self, path: str) -> dict:
        """Move a note to trash."""
        result = self._call_tool("vault_delete", {"path": path})
        return result if isinstance(result, dict) else {"raw": result}

    def vault_search(self, query: str, max_results: int = 50) -> List[dict]:
        """Full-text search across the vault.

        The server accepts `query` and an optional `path` filter. `max_results`
        is enforced client-side since the server has its own cap; we take
        the first N from the response.
        """
        result = self._call_tool("vault_search", {"query": query})
        if isinstance(result, dict):
            results = result.get("results", [])
            if isinstance(results, list):
                return list(results)[:max_results]
        return []

    # -----------------------------------------------------------------
    # Public — vault edit
    # -----------------------------------------------------------------

    def vault_edit(self, path: str, old_text: str, new_text: str) -> dict:
        """Fuzzy find-and-replace inside a note.

        Note: Connect MCP uses camelCase `oldText`/`newText` for this tool's
        arguments. This wrapper takes snake_case Python parameters and maps
        them to the wire format.
        """
        result = self._call_tool(
            "vault_edit",
            {"path": path, "oldText": old_text, "newText": new_text},
        )
        return result if isinstance(result, dict) else {"raw": result}

    def vault_edit_line(self, path: str, line: int, content: str) -> dict:
        """Replace (or insert at) a specific line in a note.

        The server uses `lineNumber` (camelCase) and defaults `mode` to
        "replace". We accept `line` as snake_case and map it onto the wire
        format.
        """
        result = self._call_tool(
            "vault_edit_line",
            {"path": path, "lineNumber": line, "content": content},
        )
        return result if isinstance(result, dict) else {"raw": result}

    def vault_patch(
        self,
        path: str,
        target_type: str,
        target: str,
        content: str,
        operation: str = "append",
    ) -> dict:
        """Edit a heading, block, or frontmatter section of a note.

        `target_type` must be "heading", "block", or "frontmatter". `target`
        is the identifier (heading path with `::`, block ID, or frontmatter
        field name). `operation` defaults to "append" — other options are
        "prepend" and "replace".
        """
        result = self._call_tool(
            "vault_patch",
            {
                "path": path,
                "targetType": target_type,
                "target": target,
                "operation": operation,
                "content": content,
            },
        )
        return result if isinstance(result, dict) else {"raw": result}

    # -----------------------------------------------------------------
    # Public — dataview & metadata
    # -----------------------------------------------------------------

    def dataview_query(self, query: str) -> List[dict]:
        """Run a DQL query. Returns a list of items/rows.

        The server's response shape depends on the query type (LIST, TABLE,
        TASK). This wrapper flattens LIST-style responses to `items`,
        TABLE-style to `rows`, and TASK-style to `tasks`. Callers that need
        the raw structured response should use `call_tool("dataview_query",
        ...)` directly.
        """
        result = self._call_tool("dataview_query", {"query": query})
        if isinstance(result, dict):
            for key in ("items", "rows", "tasks"):
                value = result.get(key)
                if isinstance(value, list):
                    return value
        return []

    def active_note(self) -> dict:
        """Return info about the currently open note in Obsidian."""
        result = self._call_tool("active_note", {})
        return result if isinstance(result, dict) else {"raw": result}

    def graph_info(self, path: str) -> dict:
        """Return link statistics for a note.

        Despite the function-style name, the upstream tool requires a note
        path — it's per-file graph metadata, not a vault-wide graph snapshot.
        """
        result = self._call_tool("graph_info", {"path": path})
        return result if isinstance(result, dict) else {"raw": result}

    def graph_links(self, path: str) -> dict:
        """Return the link list for a specific note."""
        result = self._call_tool("graph_links", {"path": path})
        return result if isinstance(result, dict) else {"raw": result}

    # -----------------------------------------------------------------
    # Public — escape hatch
    # -----------------------------------------------------------------

    def call_tool(self, name: str, arguments: dict) -> dict:
        """Invoke an arbitrary MCP tool and return the parsed result.

        Use this for tools that don't yet have a dedicated wrapper. The
        returned value is the parsed content (dict), or `{"raw": <value>}`
        if the server returned non-dict content.
        """
        result = self._call_tool(name, arguments)
        return result if isinstance(result, dict) else {"raw": result}

    # -----------------------------------------------------------------
    # Private — request plumbing
    # -----------------------------------------------------------------

    def _endpoint(self) -> str:
        return f"http://{self.host}:{self.port}/mcp"

    def _base_headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self.api_key}",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _next_id(self) -> int:
        return next(self._id_counter)

    def _ensure_initialized(self) -> None:
        """Run the initialize/notifications handshake if we haven't already.

        Idempotent — subsequent calls are no-ops. Failures propagate as
        typed exceptions from `_request`.
        """
        if self._initialized:
            return

        init_params = {
            "protocolVersion": self.MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "secondbrain-connect-mcp-client", "version": "1.0"},
        }
        # _request captures the Mcp-Session-Id response header as a side effect.
        self._request("initialize", init_params)

        # The MCP spec requires a `notifications/initialized` notification
        # after initialize. It has no response — we fire and forget.
        self._send_notification("notifications/initialized")

        self._initialized = True

    def _call_tool(self, name: str, arguments: dict) -> Any:
        """Invoke a tool and return the unwrapped content.

        Tool responses come back as `{"content": [{"type": "text", "text": "..."}]}`.
        For structured tools the text is itself a JSON string — we try to
        parse it and return the dict/list. If parsing fails, we return the
        raw string (useful for tools that legitimately return plain text).
        """
        self._ensure_initialized()
        result = self._request("tools/call", {"name": name, "arguments": arguments})

        # Sanity-check the envelope.
        if not isinstance(result, dict):
            raise ConnectMCPRequestFailed(
                f"tools/call result was not a dict: {type(result).__name__}"
            )

        content = result.get("content")
        if not isinstance(content, list) or not content:
            # Empty content is valid in theory, but we still return the outer
            # dict so callers can inspect isError etc.
            return result

        first = content[0]
        if not isinstance(first, dict):
            return result

        text = first.get("text")
        if not isinstance(text, str):
            return result

        # Try to parse the text as JSON. Most Connect MCP tools wrap structured
        # output as a JSON string; plain-text tools fall through to raw.
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def _send_notification(self, method: str) -> None:
        """Send a JSON-RPC notification (no id, no response expected).

        Connect MCP still returns HTTP 200/202 with an empty body, which we
        ignore. Network failures still map to typed exceptions — a broken
        handshake is a broken session.
        """
        body = json.dumps({"jsonrpc": "2.0", "method": method}).encode("utf-8")
        req = urllib.request.Request(
            self._endpoint(),
            data=body,
            headers=self._base_headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                # Drain the body so the connection can close cleanly.
                resp.read()
        except urllib.error.HTTPError as exc:
            self._raise_for_http_error(exc)
        except urllib.error.URLError as exc:
            raise ConnectMCPUnreachable(f"URLError during notification: {exc}") from exc
        except OSError as exc:
            raise ConnectMCPUnreachable(f"OSError during notification: {exc}") from exc

    def _request(self, method: str, params: Optional[dict] = None) -> Any:
        """Single POST /mcp round trip for a JSON-RPC call.

        Returns the unwrapped `result` field on success. Raises a typed
        exception on any failure. This is the ONE place that does network
        I/O — all public methods funnel through here.
        """
        request_id = self._next_id()
        body: Dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            body["params"] = params

        req = urllib.request.Request(
            self._endpoint(),
            data=json.dumps(body).encode("utf-8"),
            headers=self._base_headers(),
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                # Capture the session ID if the server set one. The header
                # is only present on the `initialize` response.
                session_header = resp.headers.get("Mcp-Session-Id")
                if session_header and not self._session_id:
                    self._session_id = session_header
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            self._raise_for_http_error(exc)
            # Unreachable — _raise_for_http_error always raises. This line is
            # purely for the type checker.
            raise ConnectMCPRequestFailed("unreachable")
        except urllib.error.URLError as exc:
            # URLError carries a reason that may be a socket error or a string.
            reason = exc.reason
            raise ConnectMCPUnreachable(
                f"Cannot reach Connect MCP at {self._endpoint()}: {reason}"
            ) from exc
        except OSError as exc:
            raise ConnectMCPUnreachable(
                f"OS error reaching Connect MCP at {self._endpoint()}: {exc}"
            ) from exc

        payload = self._parse_response_body(raw)
        return self._unwrap_jsonrpc(payload)

    def _raise_for_http_error(self, exc: urllib.error.HTTPError) -> None:
        """Map an HTTPError to a typed exception and raise it."""
        code = exc.code
        if code in (401, 403):
            raise ConnectMCPAuthFailed(
                f"Connect MCP auth failed (HTTP {code}): {exc.reason}"
            ) from exc
        if code == 404:
            raise ConnectMCPNotFound(
                f"Connect MCP endpoint not found (HTTP 404): {exc.reason}"
            ) from exc
        if 500 <= code < 600:
            raise ConnectMCPRequestFailed(
                f"Connect MCP server error (HTTP {code}): {exc.reason}"
            ) from exc
        raise ConnectMCPRequestFailed(
            f"Connect MCP HTTP error {code}: {exc.reason}"
        ) from exc

    def _parse_response_body(self, raw: bytes) -> Any:
        """Decode an SSE-framed JSON-RPC response body.

        Connect MCP's StreamableHTTPServerTransport wraps responses as SSE,
        e.g.:

            event: message
            data: {"jsonrpc": "2.0", "id": 1, "result": {...}}

        Some servers (or the `/health` endpoint) return plain JSON instead.
        We try SSE first, fall back to plain JSON.
        """
        if not raw:
            raise ConnectMCPRequestFailed("Connect MCP returned an empty response body")

        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ConnectMCPRequestFailed(
                f"Connect MCP response is not valid UTF-8: {exc}"
            ) from exc

        # Scan for an SSE `data:` line. There may be multiple data lines in a
        # single event; we concatenate them (per SSE spec) but for our use
        # case the server always sends one `data:` per event.
        sse_payload: Optional[str] = None
        for line in text.splitlines():
            if line.startswith("data:"):
                piece = line[5:].lstrip()
                sse_payload = piece if sse_payload is None else f"{sse_payload}\n{piece}"

        candidate = sse_payload if sse_payload is not None else text.strip()
        if not candidate:
            raise ConnectMCPRequestFailed(
                "Connect MCP response body contained no JSON payload"
            )

        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise ConnectMCPRequestFailed(
                f"Could not parse Connect MCP response as JSON: {exc}"
            ) from exc

    def _unwrap_jsonrpc(self, payload: Any) -> Any:
        """Validate a JSON-RPC envelope and return its `result` field.

        Maps MCP error codes to typed exceptions:
          - -32601 (method not found) → ConnectMCPNotFound
          - -32000..-32099 (server errors)  → ConnectMCPRequestFailed
          - anything else                    → ConnectMCPRequestFailed
        """
        if not isinstance(payload, dict):
            raise ConnectMCPRequestFailed(
                f"Connect MCP response was not a JSON-RPC object: {type(payload).__name__}"
            )

        if "error" in payload:
            error = payload.get("error")
            if isinstance(error, dict):
                code = error.get("code")
                message = error.get("message", "unknown error")
            else:
                code = None
                message = str(error)

            if code == -32601:
                raise ConnectMCPNotFound(f"Connect MCP: {message}")
            raise ConnectMCPRequestFailed(f"Connect MCP error (code={code}): {message}")

        if "result" not in payload:
            raise ConnectMCPRequestFailed(
                "Connect MCP response is missing both 'result' and 'error'"
            )

        return payload["result"]
