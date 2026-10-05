"""MCP-over-JSON-RPC 2.0 protocol layer (roadmap 2.1, step B1).

Pure message handling, no I/O: the server loop (server.py) feeds it
decoded JSON objects and writes back serialized responses. Everything
here is unit-testable without a client.

Supported surface (the thin vertical the roadmap demands):
    - initialize            -> capabilities + serverInfo
    - notifications/*       -> silently ignored (no response, per spec)
    - tools/list            -> descriptors from the tool registry
    - tools/call            -> dispatch to a tool, tool errors become
                              isError content, unknown tool -> -32602
    - ping                  -> {} (standard MCP liveness probe)

JSON-RPC error codes: -32700 parse error, -32600 invalid request,
-32601 method not found, -32602 invalid params, -32603 internal error.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Protocol

PROTOCOL_VERSION = "2024-11-05"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# A tool call returns (text, is_error); raising here is a bug -> -32603.
DispatchFn = Callable[[str, dict], tuple[str, bool]]


class ToolDescriptor(Protocol):
    """What the registry hands to tools/list (see tools.ToolDescriptor)."""

    name: str
    description: str

    def to_mcp(self) -> dict: ...


def _has_id(message: dict) -> bool:
    return "id" in message and message["id"] is not None


def make_result(request_id: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def make_error(request_id: Any, code: int, message: str, data: Any = None) -> dict:
    error: dict = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


class McpProtocol:
    """Stateless MCP method dispatch over the JSON-RPC envelope."""

    def __init__(
        self,
        server_name: str,
        server_version: str,
        tools: list[ToolDescriptor],
        dispatch: DispatchFn,
    ) -> None:
        self._server_name = server_name
        self._server_version = server_version
        self._tools = list(tools)
        self._dispatch = dispatch

    @property
    def server_info(self) -> dict:
        return {"name": self._server_name, "version": self._server_version}

    def handle(self, message: Any) -> Optional[dict]:
        """Handle one decoded message; None means 'no response'."""

        if not isinstance(message, dict):
            return make_error(None, INVALID_REQUEST, "request must be a JSON object")
        if message.get("jsonrpc") != "2.0":
            return make_error(
                message.get("id") if _has_id(message) else None,
                INVALID_REQUEST,
                'request must carry "jsonrpc": "2.0"',
            )
        method = message.get("method")
        request_id = message.get("id") if _has_id(message) else None
        if not isinstance(method, str) or not method:
            if "id" not in message:  # a notification without a method is junk input
                return None
            return make_error(request_id, INVALID_REQUEST, "method must be a non-empty string")

        # Notifications never get a response — not even an error one.
        if "id" not in message or message["id"] is None:
            return None

        params = message.get("params") or {}
        if not isinstance(params, dict):
            return make_error(request_id, INVALID_PARAMS, "params must be an object")

        if method == "initialize":
            return make_result(request_id, self._initialize_result(params))
        if method == "ping":
            return make_result(request_id, {})
        if method == "tools/list":
            return make_result(request_id, {"tools": [t.to_mcp() for t in self._tools]})
        if method == "tools/call":
            return self._call_tool(request_id, params)
        return make_error(request_id, METHOD_NOT_FOUND, f"unknown method: {method}")

    def _initialize_result(self, params: dict) -> dict:
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": self.server_info,
            # honest hint for humans reading the log
            "instructions": (
                "ai-harness-os: 1C/BSL generation with live verification. "
                "Call ping for versions, benchmark_info for the task set, "
                "verify_module to check BSL text, run_loop to generate code."
            ),
        }

    def _call_tool(self, request_id: Any, params: dict) -> dict:
        name = params.get("name")
        if not isinstance(name, str) or not name:
            return make_error(request_id, INVALID_PARAMS, "params.name must be a tool name")
        known = {t.name for t in self._tools}
        if name not in known:
            return make_error(request_id, INVALID_PARAMS, f"unknown tool: {name}")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return make_error(request_id, INVALID_PARAMS, "params.arguments must be an object")
        try:
            text, is_error = self._dispatch(name, arguments)
        except Exception as exc:  # noqa: BLE001 — a tool bug is an internal error, not a crash
            return make_error(
                request_id, INTERNAL_ERROR, f"tool {name} crashed: {exc}", data=str(exc)
            )
        return make_result(
            request_id,
            {
                "content": [{"type": "text", "text": text}],
                "isError": bool(is_error),
            },
        )
