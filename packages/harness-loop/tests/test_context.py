"""Context layer tests (roadmap 2.1, phase C): builtin indexer, mcp backend,
resolver, loop wiring — everything on fakes/tmp trees, no network."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from harness_loop.context import (
    BuiltInIndexer,
    ContextResult,
    McpIndexerBackend,
    collect_context,
    estimate_tokens,
    resolve_provider,
)
from harness_loop.loop import BslAgentLoop


def make_project(root: Path) -> Path:
    (root / "src" / "catalog").mkdir(parents=True)
    (root / "src" / "catalog" / "Номенклатура.bsl").write_text(
        textwrap.dedent(
            """
            Функция ЦенаНоменклатуры(Ссылка) Экспорт
                Возврат 0;
            КонецФункции

            Процедура ЗаполнитьЦены(Товары) Экспорт
            КонецПроцедуры
            """
        ),
        encoding="utf-8",
    )
    (root / "src" / "http").mkdir(parents=True)
    (root / "src" / "http" / "Клиенты.bsl").write_text(
        "Функция ОтдатьКлиентов() Экспорт\n"
        "    В = Запрос.Ссылка; // Справочник.Контрагенты\n"
        "    Возврат В;\n"
        "КонецФункции\n",
        encoding="utf-8",
    )
    (root / "src" / "deploy.os").write_text(
        "Процедура ВыгрузитьВсё(Каталог) Экспорт\nКонецПроцедуры\n", encoding="utf-8"
    )
    (root / "readme.md").write_text("не BSL", encoding="utf-8")
    return root


class TestBuiltInIndexer:
    def test_signatures_and_metadata_extracted(self, tmp_path):
        project = make_project(tmp_path)
        result = BuiltInIndexer().collect(str(project), "напиши функцию цены номенклатуры")
        assert not result.empty
        assert result.source == "builtin"
        assert result.modules_scanned == 3
        assert "Функция ЦенаНоменклатуры(Ссылка) Экспорт" in result.text
        assert "Справочник.Контрагенты" in result.text  # metadata found
        assert "deploy.os" in result.text  # .os files included

    def test_relevance_ranks_matching_module_first(self, tmp_path):
        project = make_project(tmp_path)
        result = BuiltInIndexer().collect(str(project), "заполнить цены товаров")
        catalog_block = result.text.index("Номенклатура.bsl")
        clients_block = result.text.index("Клиенты.bsl")
        assert catalog_block < clients_block

    def test_budget_is_respected(self, tmp_path):
        project = make_project(tmp_path)
        result = BuiltInIndexer().collect(str(project), "номенклатура", max_tokens=60)
        assert estimate_tokens(result.text) <= 60 + 20  # header allowance
        assert result.modules_selected >= 1

    def test_missing_path_is_a_note_not_a_crash(self):
        result = BuiltInIndexer().collect("/no/such/dir", "задача")
        assert result.empty
        assert "not found" in result.note

    def test_empty_project_reported_honestly(self, tmp_path):
        result = BuiltInIndexer().collect(str(tmp_path), "задача")
        assert result.empty
        assert "no .bsl/.os" in result.note

    def test_bad_budget_rejected(self):
        with pytest.raises(ValueError):
            BuiltInIndexer(token_budget=10)


class TestEstimateTokens:
    def test_roughly_four_chars_per_token(self):
        assert estimate_tokens("x" * 40) == 10
        assert estimate_tokens("") == 1


FAKE_MCP_SERVER = textwrap.dedent(
    """
    import json, sys
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        mid = msg.get("id")
        method = msg.get("method")
        if method == "initialize":
            out = {"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake-index", "version": "0.0.1"}}}
        elif method == "tools/list":
            out = {"jsonrpc": "2.0", "id": mid, "result": {"tools": [
                {"name": "search_definitions"},
                {"name": "get_project_structure"}]}}
        elif method == "tools/call":
            out = {"jsonrpc": "2.0", "id": mid, "result": {"content": [
                {"type": "text", "text": "модуль: Цены.bsl\\nФункция ЦенаТовара(Т)"}],
                "isError": False}}
        else:
            out = {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "nope"}}
        sys.stdout.write(json.dumps(out) + "\\n")
        sys.stdout.flush()
    """
)


class TestMcpIndexerBackend:
    def test_full_roundtrip_against_fake_server(self, tmp_path):
        script = tmp_path / "fake_mcp.py"
        script.write_text(FAKE_MCP_SERVER, encoding="utf-8")
        backend = McpIndexerBackend(sys.executable, args=[str(script)], preferred_tools=["search"])
        result = backend.collect(str(tmp_path), "цены")
        assert result.source == "mcp"
        assert not result.empty
        assert "search_definitions" in result.text  # header names the tool used
        assert "Функция ЦенаТовара(Т)" in result.text

    def test_missing_binary_returns_fallback_note(self):
        backend = McpIndexerBackend("/no/such/mcp-binary")
        result = backend.collect("/tmp", "задача")
        assert result.empty
        assert "not found" in result.note

    def test_dead_server_is_caught_not_raised(self, tmp_path):
        dead = tmp_path / "dead.py"
        dead.write_text("import sys\nsys.exit(1)\n", encoding="utf-8")
        backend = McpIndexerBackend(sys.executable, args=[str(dead)])
        result = backend.collect("/tmp", "задача")
        assert result.source == "mcp"
        assert result.note  # failure documented, exception swallowed


class TestCollectContextFallback:
    def test_mcp_failure_falls_back_to_builtin(self, tmp_path):
        project = make_project(tmp_path)
        broken = McpIndexerBackend("/no/such/mcp-binary")
        result = collect_context(broken, str(project), "цены номенклатуры")
        assert result.source == "builtin"
        assert "mcp" in result.note  # warning preserved
        assert "ЦенаНоменклатуры" in result.text

    def test_healthy_builtin_passes_through(self, tmp_path):
        project = make_project(tmp_path)
        result = collect_context(BuiltInIndexer(), str(project), "цены")
        assert result.source == "builtin"
        assert result.note == ""


class TestResolveProvider:
    def test_default_is_builtin(self):
        assert resolve_provider({}).name == "builtin"

    def test_none_disables(self):
        assert resolve_provider({"HARNESS_CONTEXT": "none"}) is None

    def test_mcp_without_path_degrades_to_builtin(self):
        provider = resolve_provider({"HARNESS_CONTEXT": "mcp"})
        assert provider.name == "builtin"

    def test_mcp_with_path(self):
        provider = resolve_provider(
            {"HARNESS_CONTEXT": "mcp", "CODE_INDEX_MCP_PATH": "/bin/indexer"}
        )
        assert provider.name == "mcp"


class TestLoopWiring:
    """C3: the provider fills the context socket; IterationLog observes it."""

    def test_provider_collects_when_context_empty(self, tmp_path):
        captured = {}

        class FakeProvider:
            name = "builtin"

            def collect(self, project_path, task, max_tokens=8000):
                captured["project_path"] = project_path
                captured["task"] = task
                return ContextResult(
                    text="== контекст ==\nФункция Эталон()", tokens=7, source="builtin"
                )

        loop = BslAgentLoop(
            llm=FakeLLM(),
            verifier=FakeVerifier(),
            context_provider=FakeProvider(),
            project_path=str(tmp_path),
        )
        result = loop.run("сделай функцию")
        assert captured["project_path"] == str(tmp_path)
        assert captured["task"] == "сделай функцию"
        assert result.iterations[0].context_source == "builtin"
        assert result.iterations[0].context_tokens == 7
        assert "контекст" in FakeLLM.last_user_prompt

    def test_explicit_context_wins_over_provider(self, tmp_path):
        class BoomProvider:
            name = "builtin"

            def collect(self, *args, **kwargs):
                raise AssertionError("provider must not run when context is given")

        loop = BslAgentLoop(
            llm=FakeLLM(),
            verifier=FakeVerifier(),
            context_provider=BoomProvider(),
            project_path=str(tmp_path),
        )
        result = loop.run("задача", context="явный контекст")
        assert "явный контекст" in FakeLLM.last_user_prompt
        assert result.iterations[0].context_source == ""  # provider never ran

    def test_no_provider_no_project_path_is_the_old_behaviour(self):
        loop = BslAgentLoop(llm=FakeLLM(), verifier=FakeVerifier())
        result = loop.run("задача")
        assert result.iterations[0].context_source == ""
        assert result.iterations[0].context_tokens == 0


class FakeLLM:
    """Minimal LLMPort: returns one BSL module; records the last prompt."""

    last_user_prompt = ""

    def complete(self, messages, **kwargs):
        from russian_llm_pack.types import CompletionResult, UsageInfo

        FakeLLM.last_user_prompt = messages[-1].content
        return CompletionResult(
            text="```bsl\nФункция Решение()\nКонецФункции\n```",
            provider="fake",
            model="fake",
            usage=UsageInfo(input_tokens=10, output_tokens=10),
            latency_ms=1.0,
        )


class FakeVerifier:
    """Minimal verifier port: everything passes."""

    def verify_module_text(self, text, *, filename="module.bsl"):
        from bsl_verify.types import FileReport, VerifyResult

        return VerifyResult(files=[FileReport(path=filename, diagnostics=[])], passed=True)
