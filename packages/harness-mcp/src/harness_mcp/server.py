"""harness-mcp: stdio MCP server exposing ai-harness-os to coding agents.

Five tools over the existing ports (roadmap 2.1, phase B):

    ping           — health-check with package versions
    benchmark_info — profile of the bundled SWE-bench-BSL set
    verify_module  — L0 static verification of a BSL module text
    run_loop       — generate → verify → (judge) loop, cached by run_id
    eval_summary   — benchmark run, default-limited to 10 tasks

Transport: one JSON-RPC 2.0 message per line over stdin/stdout (the MCP
stdio transport). Wire traffic is ASCII-escaped JSON (ensure_ascii), so
the pipe is safe on any locale, Windows consoles included; tool texts
stay fully readable after the client decodes them.
"""

from __future__ import annotations

import json
import sys
from typing import IO, Optional

from .protocol import McpProtocol, make_error
from .tools import HarnessServices, ToolOutcome, build_registry


class HarnessMcpServer:
    """The MCP server object: registry + services + protocol dispatch."""

    NAME = "harness-mcp"

    def __init__(self, services: Optional[HarnessServices] = None):
        self.services = services or HarnessServices()
        self._registry = build_registry()
        self._by_name = {descriptor.name: handler for descriptor, handler in self._registry}
        from . import __version__

        self._protocol = McpProtocol(
            server_name=self.NAME,
            server_version=__version__,
            tools=[descriptor for descriptor, _handler in self._registry],
            dispatch=self._dispatch,
        )

    def handle_message(self, message: object) -> Optional[dict]:
        """One decoded JSON-RPC message -> response dict or None."""

        return self._protocol.handle(message)

    def _dispatch(self, name: str, arguments: dict) -> tuple[str, bool]:
        handler = self._by_name[name]
        outcome: ToolOutcome = handler(self.services, arguments)
        return outcome.text, outcome.is_error


def serve(stdin: IO[str], stdout: IO[str]) -> int:
    """The stdio loop: read lines, write responses, exit 0 on EOF.

    Malformed lines get a -32700 response and the loop continues; a
    broken stdout raises, which is the caller's crash, not ours.
    """

    server = HarnessMcpServer()
    for raw_line in stdin:
        line = raw_line.strip()
        if not line:
            continue  # keep-alive whitespace between messages
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            response = make_error(None, -32700, f"parse error: {exc}")
        else:
            response = server.handle_message(message)
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()
    return 0


def main(argv: Optional[list] = None) -> int:
    """Console entry point (`harness-mcp` / `python -m harness_mcp`).

    `--version` is handled before the stdio loop: repo CLI convention
    (every entry point answers --version without starting the server).
    """

    args = sys.argv[1:] if argv is None else list(argv)
    if "--version" in args:
        from . import __version__

        print(f"harness-mcp {__version__}")
        return 0
    return serve(sys.stdin, sys.stdout)


if __name__ == "__main__":  # pragma: no cover — exercised via subprocess smoke
    sys.exit(main())
