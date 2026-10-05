"""Server tests: in-process stdio loop + a REAL subprocess smoke.

The subprocess smoke spawns `python -m harness_mcp`, speaks MCP to it
over real pipes (the way Claude Code / opencode / Cline would) and
calls ALL FIVE tools — the roadmap 2.1 phase B acceptance gate. Tools
that need java/keys degrade to honest isError text; the protocol never
breaks. No network, no java, no keys required.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path


from harness_mcp.server import HarnessMcpServer, serve

SRC = Path(__file__).parent.parent / "src"


class TestServeLoop:
    def _roundtrip(self, lines: list[str]) -> list[dict]:
        stdin = io.StringIO("\n".join(lines) + "\n")
        stdout = io.StringIO()
        rc = serve(stdin, stdout)
        assert rc == 0
        return [json.loads(line) for line in stdout.getvalue().splitlines()]

    def test_initialize_then_tools_list(self):
        responses = self._roundtrip(
            [
                '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}',
                '{"jsonrpc":"2.0","method":"notifications/initialized"}',
                '{"jsonrpc":"2.0","id":2,"method":"tools/list"}',
            ]
        )
        assert len(responses) == 2  # the notification got no response
        assert responses[0]["result"]["serverInfo"]["name"] == "harness-mcp"
        names = [t["name"] for t in responses[1]["result"]["tools"]]
        assert names == ["ping", "benchmark_info", "verify_module", "run_loop", "eval_summary"]

    def test_parse_error_gets_32700_and_loop_survives(self):
        responses = self._roundtrip(
            [
                "это не json",
                '{"jsonrpc":"2.0","id":7,"method":"ping"}',
            ]
        )
        assert responses[0]["error"]["code"] == -32700
        assert responses[1]["result"] == {}

    def test_blank_lines_are_skipped(self):
        responses = self._roundtrip(
            ["", "   ", '{"jsonrpc":"2.0","id":1,"method":"ping"}', ""]
        )
        assert len(responses) == 1

    def test_unknown_method(self):
        responses = self._roundtrip(['{"jsonrpc":"2.0","id":1,"method":"nope"}'])
        assert responses[0]["error"]["code"] == -32601


class TestServerObject:
    def test_dispatch_routes_to_registry(self):
        server = HarnessMcpServer()
        response = server.handle_message(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "ping"}}
        )
        text = response["result"]["content"][0]["text"]
        assert "harness_mcp 0.1.1" in text


class TestSubprocessSmoke:
    """The phase B acceptance gate: a real client process, five real tools."""

    def _talk(self, server: subprocess.Popen, requests: list[dict]) -> list[dict]:
        """Send all requests, read exactly one response per request WITH an id.

        Notifications (no id / null id) never get a response — reading a
        line for them would deadlock against a spec-correct server.
        """

        expecting = [r for r in requests if r.get("id") is not None]
        for request in requests:
            server.stdin.write(json.dumps(request) + "\n")
        server.stdin.flush()
        responses = []
        for _ in expecting:
            line = server.stdout.readline()
            if not line:
                break
            responses.append(json.loads(line))
        return responses

    def test_all_five_tools_over_real_pipes(self):
        server = subprocess.Popen(
            [sys.executable, "-m", "harness_mcp"],
            cwd=str(SRC.parent.parent),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        try:
            requests = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {
                    "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {"name": "ping", "arguments": {}},
                },
                {
                    "jsonrpc": "2.0", "id": 4, "method": "tools/call",
                    "params": {"name": "benchmark_info", "arguments": {}},
                },
                {
                    "jsonrpc": "2.0", "id": 5, "method": "tools/call",
                    "params": {"name": "verify_module", "arguments": {"bsl_text": "Функция А("}},
                },
                {
                    "jsonrpc": "2.0", "id": 6, "method": "tools/call",
                    "params": {"name": "run_loop", "arguments": {"task": "функция суммы"}},
                },
                {
                    "jsonrpc": "2.0", "id": 7, "method": "tools/call",
                    "params": {"name": "eval_summary", "arguments": {"limit": 2}},
                },
            ]
            responses = self._talk(server, requests)
        finally:
            server.stdin.close()
            server.wait(timeout=30)

        by_id = {r["id"]: r for r in responses}
        # initialize
        assert by_id[1]["result"]["serverInfo"]["name"] == "harness-mcp"
        # tools/list: exactly the five tools
        names = [t["name"] for t in by_id[2]["result"]["tools"]]
        assert names == ["ping", "benchmark_info", "verify_module", "run_loop", "eval_summary"]
        # ping: versions present, isError False
        ping = by_id[3]["result"]
        assert ping["isError"] is False
        assert "harness_loop" in ping["content"][0]["text"]
        # benchmark_info: the real bundled set
        bench = by_id[4]["result"]
        assert "70 tasks" in bench["content"][0]["text"]
        assert "59/70 (84%)" in bench["content"][0]["text"]
        # verify_module without java -> honest tool error, protocol intact
        verify = by_id[5]["result"]
        assert verify["isError"] is True
        assert "verifier unavailable" in verify["content"][0]["text"]
        # run_loop without LLM keys -> honest failure report with run_id
        run_loop = by_id[6]["result"]
        run_text = run_loop["content"][0]["text"]
        assert "run_id: run-" in run_text
        assert "BSL loop: FAILED" in run_text
        # eval_summary limited to 2 tasks, LLM-less failure is data, not a crash
        ev = by_id[7]["result"]
        ev_text = ev["content"][0]["text"]
        assert "tasks: 2 (full=false)" in ev_text
        # stderr stays quiet: no crashes, no tracebacks
        assert server.stderr.read() == ""


class TestMainVersion:
    def test_version_flag_answers_without_starting_the_server(self, capsys):
        from harness_mcp.server import main

        rc = main(["--version"])
        assert rc == 0
        assert "harness-mcp 0.1.1" in capsys.readouterr().out
