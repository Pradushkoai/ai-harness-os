"""Protocol layer tests: JSON-RPC envelope + MCP methods, no I/O."""

from __future__ import annotations

import pytest

from harness_mcp.protocol import (
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PROTOCOL_VERSION,
    McpProtocol,
)
from harness_mcp.tools import ToolDescriptor


def make_protocol(tools=None, dispatch=None):
    descriptors = tools or [ToolDescriptor(name="echo", description="echo tool")]
    if dispatch is None:
        def dispatch(name, arguments):
            return (f"echo: {arguments.get('text', '')}", False)
    return McpProtocol(
        server_name="test-server",
        server_version="9.9.9",
        tools=descriptors,
        dispatch=dispatch,
    )


class TestInitialize:
    def test_returns_capabilities_and_server_info(self):
        response = make_protocol().handle(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        )
        result = response["result"]
        assert result["protocolVersion"] == PROTOCOL_VERSION
        assert result["capabilities"]["tools"]["listChanged"] is False
        assert result["serverInfo"] == {"name": "test-server", "version": "9.9.9"}

    def test_echoes_jsonrpc_and_id(self):
        response = make_protocol().handle({"jsonrpc": "2.0", "id": "abc", "method": "ping"})
        assert response == {"jsonrpc": "2.0", "id": "abc", "result": {}}


class TestNotifications:
    def test_initialized_notification_gets_no_response(self):
        assert (
            make_protocol().handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
            is None
        )

    def test_unknown_notification_is_silently_ignored(self):
        message = {"jsonrpc": "2.0", "method": "notifications/whatever"}
        assert make_protocol().handle(message) is None


class TestEnvelopeValidation:
    def test_non_object_message_is_invalid_request(self):
        response = make_protocol().handle([1, 2, 3])
        assert response["error"]["code"] == INVALID_REQUEST

    def test_wrong_jsonrpc_version_rejected(self):
        response = make_protocol().handle(
            {"jsonrpc": "1.0", "id": 1, "method": "ping"}
        )
        assert response["error"]["code"] == INVALID_REQUEST

    def test_missing_method_with_id_is_invalid_request(self):
        response = make_protocol().handle({"jsonrpc": "2.0", "id": 5})
        assert response["error"]["code"] == INVALID_REQUEST

    def test_params_must_be_object(self):
        response = make_protocol().handle(
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": [1, 2]}
        )
        assert response["error"]["code"] == INVALID_PARAMS


class TestTools:
    def test_tools_list_exposes_descriptors_with_schema(self):
        tool = ToolDescriptor(
            name="echo",
            description="echoes",
            input_schema={"text": {"type": "string"}},
        )
        response = make_protocol(tools=[tool]).handle(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        )
        listed = response["result"]["tools"][0]
        assert listed["name"] == "echo"
        assert listed["description"] == "echoes"
        assert listed["inputSchema"]["type"] == "object"
        assert listed["inputSchema"]["properties"]["text"]["type"] == "string"

    def test_tools_call_wraps_text_content(self):
        response = make_protocol().handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "echo", "arguments": {"text": "привет"}},
            }
        )
        result = response["result"]
        assert result["content"] == [{"type": "text", "text": "echo: привет"}]
        assert result["isError"] is False

    def test_unknown_tool_is_invalid_params(self):
        response = make_protocol().handle(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "nope", "arguments": {}},
            }
        )
        assert response["error"]["code"] == INVALID_PARAMS
        assert "nope" in response["error"]["message"]

    def test_tool_crash_is_internal_error_not_a_crash(self):
        def broken(name, arguments):
            raise RuntimeError("boom")

        response = make_protocol(dispatch=broken).handle(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "echo", "arguments": {}},
            }
        )
        assert response["error"]["code"] == -32603
        assert "boom" in response["error"]["message"]

    def test_tool_is_error_flag_surfaces_in_content(self):
        def failing(name, arguments):
            return ("верификатор недоступен", True)

        response = make_protocol(dispatch=failing).handle(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "echo", "arguments": {}},
            }
        )
        assert response["result"]["isError"] is True


class TestMethodNotFound:
    def test_unknown_method_with_id(self):
        response = make_protocol().handle({"jsonrpc": "2.0", "id": 9, "method": "wat"})
        assert response["error"]["code"] == METHOD_NOT_FOUND


@pytest.mark.parametrize(
    "message",
    [
        {"jsonrpc": "2.0", "id": None, "method": "ping"},  # explicit null id = notification
        {"jsonrpc": "2.0", "method": "ping"},  # no id at all
    ],
)
def test_null_id_messages_are_notifications(message):
    assert make_protocol().handle(message) is None
