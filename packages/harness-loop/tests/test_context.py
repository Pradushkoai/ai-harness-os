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

    # -- real-config hardening (found on a UT 11 dump: 7141 .bsl modules) --

    def test_bom_does_not_hide_first_signature(self, tmp_path):
        # every module of a real EDT dump starts with a UTF-8 BOM; \ufeff is
        # not matched by \s* so the first signature of the file was lost
        body = (
            "Процедура ПерваяСтрокой() Экспорт\nКонецПроцедуры\n"
            "Процедура Вторая() Экспорт\nКонецПроцедуры\n"
        ).encode("utf-8")
        (tmp_path / "СБомом.bsl").write_bytes(b"\xef\xbb\xbf" + body)
        result = BuiltInIndexer().collect(str(tmp_path), "задача")
        assert "ПерваяСтрокой" in result.text

    def test_multibyte_char_cut_by_head_limit_is_dropped_not_mangled(self, tmp_path):
        # the 64 KiB head slice can cut a Cyrillic letter in half; the old
        # decode(errors="replace") injected U+FFFD into the text
        filler = ("//" + "я" * 30 + "\n").encode("utf-8")  # 63 bytes per line
        while len(filler) < 300:
            filler += filler
        (tmp_path / "Обрезка.bsl").write_bytes(
            filler[:100] + "\nПроцедура Целая() Экспорт\nКонецПроцедуры\n".encode("utf-8")
        )
        indexer = BuiltInIndexer(max_files=2, max_file_bytes=100)
        result = indexer.collect(str(tmp_path), "задача")
        assert "\ufffd" not in result.text

    def test_cap_cuts_by_path_relevance_not_alphabet(self, tmp_path):
        # a real config carries thousands of modules; with a small cap the
        # alphabetical first files used to crowd out the relevant ones
        for name in ("ААА Первый.bsl", "БББ Второй.bsl", "ВВВ Третий.bsl"):
            (tmp_path / name).write_text(
                "Процедура Пустая() Экспорт\nКонецПроцедуры\n", encoding="utf-8"
            )
        (tmp_path / "Склад").mkdir()
        (tmp_path / "Склад" / "ОстаткиТоваров.bsl").write_text(
            "Функция Остатки() Экспорт\nКонецФункции\n", encoding="utf-8"
        )
        result = BuiltInIndexer(max_files=1).collect(
            str(tmp_path), "проверить остатки товаров на складе"
        )
        assert result.modules_scanned == 1
        assert "ОстаткиТоваров.bsl" in result.text
        assert "ААА" not in result.text

    def test_comment_inside_params_is_stripped(self, tmp_path):
        # real UT module: ПриВыгрузкеДанных(СтандартнаяОбработка, // HS\n
        # Структура) — the comment leaked into the rendered params line
        (tmp_path / "Обмен.bsl").write_text(
            "Функция ПриВыгрузке(СтандартнаяОбработка,\n"
            "\t\t\t// HS\n"
            "\t\t\tСтруктура) Экспорт\n"
            "КонецФункции\n",
            encoding="utf-8",
        )
        result = BuiltInIndexer().collect(str(tmp_path), "выгрузка")
        assert "HS" not in result.text
        assert "Структура" in result.text

    def test_long_params_end_with_ellipsis(self, tmp_path):
        long_param = "П" + "рам" * 80
        (tmp_path / "Длинный.bsl").write_text(
            f"Функция Длинная({long_param}) Экспорт\nКонецФункции\n", encoding="utf-8"
        )
        result = BuiltInIndexer().collect(str(tmp_path), "длинная")
        line = next(ln for ln in result.text.split("\n") if "Длинная(" in ln)
        assert "…" in line  # truncation marker inside the params

    def test_clipped_signature_count_is_marked(self, tmp_path):
        lines = [
            f"Процедура Шаг{я:02d}() Экспорт\nКонецПроцедуры\n" for я in range(45)
        ]
        (tmp_path / "Много.bsl").write_text("".join(lines), encoding="utf-8")
        result = BuiltInIndexer().collect(str(tmp_path), "шаг")
        assert "(и ещё 5 сигнатур" in result.text

    def test_env_max_files_override(self, tmp_path, monkeypatch):
        for i in range(5):
            (tmp_path / f"М{i}.bsl").write_text(
                "Процедура П() Экспорт\nКонецПроцедуры\n", encoding="utf-8"
            )
        monkeypatch.setenv("HARNESS_CONTEXT_MAX_FILES", "2")
        result = BuiltInIndexer().collect(str(tmp_path), "задача")
        assert result.modules_scanned == 2
        monkeypatch.setenv("HARNESS_CONTEXT_MAX_FILES", "не число")
        result = BuiltInIndexer().collect(str(tmp_path), "задача")
        assert result.modules_scanned == 5  # falls back to the 500 default

    def test_bad_max_files_rejected(self):
        with pytest.raises(ValueError):
            BuiltInIndexer(max_files=0)

    def test_collect_twice_reads_files_once(self, tmp_path, monkeypatch):
        """E-3: one eval re-collects per task — the indexer instance caches.

        The second collect() for the same root must not re-read module
        heads (minutes of IO on a real 613+ module dump × 12 tasks).
        """
        module = tmp_path / "ОбщегоНазначения.bsl"
        module.write_text(
            "Функция КурсыВалют() Экспорт\n    Возврат 0;\nКонецФункции\n",
            encoding="utf-8",
        )
        reads = {"n": 0}
        real_read = Path.read_bytes

        def counting_read(self, *args, **kwargs):
            reads["n"] += 1
            return real_read(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_bytes", counting_read)
        indexer = BuiltInIndexer()
        first = indexer.collect(str(tmp_path), "валюты курс")
        second = indexer.collect(str(tmp_path), "другая задача — тот же проект")

        assert reads["n"] == 1  # the file was parsed exactly once
        # per-task relevance still differs (rendering is task-dependent),
        # but the module came from the cache, not from a second read
        assert "ОбщегоНазначения" in first.text
        assert "ОбщегоНазначения" in second.text
        assert first.modules_scanned == second.modules_scanned == 1
        assert first.source == second.source == "builtin"


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

    def test_env_note_rides_alongside_collected_context(self, tmp_path):
        """Regression (E-3): the engine note must not eat the context socket.

        run_eval used to prepend ONESCRIPT_ENGINE_NOTE into `context` before
        calling loop.run — the non-empty socket then disabled provider
        collection, so live evals (L1 available) never saw project context.
        The note now arrives as `env_note` and rides AFTER the collected
        project context; telemetry (context_source/tokens) reflects the
        provider, not the note.
        """

        class NoteProvider:
            name = "builtin"

            def collect(self, project_path, task, max_tokens=8000):
                return ContextResult(
                    text="== контекст проекта ==\nФункция Эталон()",
                    tokens=9,
                    source="builtin",
                )

        loop = BslAgentLoop(
            llm=FakeLLM(),
            verifier=FakeVerifier(),
            context_provider=NoteProvider(),
            project_path=str(tmp_path),
        )
        result = loop.run("задача", env_note="СРЕДА: OneScript 2.2.0")

        prompt = FakeLLM.last_user_prompt
        # BOTH reached the generator; the note leads (as in 0.13.0),
        # the project context follows — order is stable and tested
        assert "контекст проекта" in prompt
        assert "OneScript 2.2.0" in prompt
        assert prompt.index("OneScript 2.2.0") < prompt.index("контекст проекта")
        # telemetry describes the provider part only
        assert result.iterations[0].context_source == "builtin"
        assert result.iterations[0].context_tokens == 9

    def test_env_note_alone_still_reaches_the_prompt(self):
        loop = BslAgentLoop(llm=FakeLLM(), verifier=FakeVerifier())
        result = loop.run("задача", env_note="СРЕДА: OneScript 2.2.0")

        assert "OneScript 2.2.0" in FakeLLM.last_user_prompt
        assert result.iterations[0].context_source == ""


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
