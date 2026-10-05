"""harness-mcp: stdio MCP server exposing ai-harness-os to coding agents.

Phase B of roadmap 2.1: the MCP OUTPUT the repo was missing — Claude
Code / opencode / Cline get the harness as five tools over the existing
ports (no new engine logic):

    ping, benchmark_info, verify_module, run_loop, eval_summary

Session state (B3): run_loop/eval_summary results live in memory with a
15-minute TTL under opaque run_ids, so an agent can re-ask for details
without re-running the loop. No persistence — a deliberate v0.1 limit.
"""

from .protocol import McpProtocol, ToolDescriptor as ProtocolToolDescriptor
from .server import HarnessMcpServer, main, serve
from .state import SessionState
from .tools import HarnessServices, ToolDescriptor, ToolOutcome, build_registry

__version__ = "0.1.1"

__all__ = [
    "HarnessMcpServer",
    "HarnessServices",
    "McpProtocol",
    "ProtocolToolDescriptor",
    "SessionState",
    "ToolDescriptor",
    "ToolOutcome",
    "build_registry",
    "main",
    "serve",
    "__version__",
]
