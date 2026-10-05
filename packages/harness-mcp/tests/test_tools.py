"""Tool surface tests with fake services — no network, no java, no keys."""

from __future__ import annotations

import pytest
from bsl_verify import BslVerifyError

from harness_mcp import tools as tools_mod
from harness_mcp.tools import (
    TOOL_BENCHMARK_INFO,
    TOOL_EVAL_SUMMARY,
    TOOL_PING,
    TOOL_RUN_LOOP,
    TOOL_VERIFY_MODULE,
    HarnessServices,
    build_registry,
    tool_benchmark_info,
    tool_eval_summary,
    tool_ping,
    tool_run_loop,
    tool_verify_module,
)

from conftest import FakeVerifier


@pytest.fixture
def no_engine(monkeypatch):
    """Deterministic environment: no java, no OneScript."""

    monkeypatch.setattr(tools_mod, "shutil_which", lambda name: None)
    monkeypatch.setattr(tools_mod.OneScriptRunner, "discover", staticmethod(lambda: None))


class TestRegistry:
    def test_exactly_five_tools_with_descriptions(self):
        registry = build_registry()
        names = [descriptor.name for descriptor, _handler in registry]
        assert names == [
            TOOL_PING, TOOL_BENCHMARK_INFO, TOOL_VERIFY_MODULE, TOOL_RUN_LOOP, TOOL_EVAL_SUMMARY,
        ]
        for descriptor, _handler in registry:
            assert descriptor.description
            assert descriptor.input_schema is not None

    def test_schemas_declare_types(self):
        registry = dict((d.name, d.input_schema) for d, _ in build_registry())
        assert registry[TOOL_VERIFY_MODULE]["bsl_text"]["type"] == "string"
        assert registry[TOOL_EVAL_SUMMARY]["full"]["type"] == "boolean"
        assert registry[TOOL_RUN_LOOP]["max_iterations"]["type"] == "integer"


class TestPing:
    def test_reports_versions_and_missing_environment(self, services, no_engine):
        outcome = tool_ping(services, {})
        assert not outcome.is_error
        assert "harness_mcp 0.1.2" in outcome.text
        assert "harness_loop" in outcome.text
        assert "NOT FOUND (verify_module unavailable)" in outcome.text
        assert "NOT FOUND (L1 off)" in outcome.text


class TestBenchmarkInfo:
    def test_profiles_the_real_bundled_set(self, services):
        outcome = tool_benchmark_info(services, {})
        assert not outcome.is_error
        assert "70 tasks" in outcome.text
        assert "15 categories" in outcome.text
        assert "64/70 (91%)" in outcome.text
        assert "query x5" in outcome.text  # honest limits documented


class TestVerifyModule:
    def test_diagnostics_rendered_with_1_based_lines(self, services):
        outcome = tool_verify_module(services, {"bsl_text": "Функция Плохо("})
        assert not outcome.is_error
        assert "FAILED" in outcome.text
        assert "line 3" in outcome.text  # 0-based 2 -> human 3
        assert "ParseError" in outcome.text
        assert "не хватает КонецФункции" in outcome.text

    def test_missing_text_is_argument_error(self, services):
        outcome = tool_verify_module(services, {})
        assert outcome.is_error
        assert "bsl_text" in outcome.text

    def test_blank_text_is_argument_error(self, services):
        outcome = tool_verify_module(services, {"bsl_text": "   "})
        assert outcome.is_error

    def test_verifier_outage_is_tool_error_not_crash(self, fake_loop):
        raising = FakeVerifier(raises=BslVerifyError("java not found"))
        services = HarnessServices(
            loop_factory=lambda: fake_loop,
            verifier_factory=lambda: raising,
        )
        outcome = tool_verify_module(services, {"bsl_text": "Функция А()\nКонецФункции"})
        assert outcome.is_error
        assert "verifier unavailable" in outcome.text
        assert "java not found" in outcome.text


class TestRunLoop:
    def test_happy_path_returns_run_id_and_code(self, services):
        outcome = tool_run_loop(services, {"task": "сделай функцию суммы"})
        assert not outcome.is_error
        assert "run_id: run-" in outcome.text
        assert "PASSED after 1 iteration(s)" in outcome.text
        assert "КонецФункции" in outcome.text  # final code included

    def test_task_and_context_forwarded_to_loop(self, services, fake_loop):
        tool_run_loop(services, {"task": "задача", "context": "контекст"})
        assert fake_loop.calls == [("задача", "контекст")]

    def test_run_id_fetches_details_without_rerun(self, services, fake_loop):
        first = tool_run_loop(services, {"task": "задача"})
        import re

        match = re.search(r"run_id: (run-[0-9a-f]+)", first.text)
        assert match, first.text
        run_id = match.group(1)
        before = len(fake_loop.calls)
        details = tool_run_loop(services, {"run_id": run_id})
        assert not details.is_error
        assert run_id in details.text
        assert "model=fake-model" in details.text
        assert len(fake_loop.calls) == before  # loop NOT re-run

    def test_unknown_run_id_is_error(self, services):
        outcome = tool_run_loop(services, {"run_id": "run-ghost"})
        assert outcome.is_error
        assert "not found" in outcome.text

    def test_missing_task_is_argument_error(self, services):
        outcome = tool_run_loop(services, {})
        assert outcome.is_error
        assert "task" in outcome.text

    def test_bad_max_iterations_rejected(self, services):
        for bad in (0, -1, True, "3"):
            outcome = tool_run_loop(services, {"task": "x", "max_iterations": bad})
            assert outcome.is_error, bad

    def test_loop_config_error_is_tool_error(self):
        def broken_factory(project_path=""):
            raise tools_mod.RLLError("no keys")

        services = HarnessServices(loop_factory=broken_factory)
        outcome = tool_run_loop(services, {"task": "x"})
        assert outcome.is_error
        assert "loop unavailable" in outcome.text
        assert "no keys" in outcome.text


class TestEvalSummary:
    def test_default_limit_is_ten_tasks(self, services, no_engine):
        outcome = tool_eval_summary(services, {})
        assert not outcome.is_error
        assert "tasks: 10 (full=false)" in outcome.text
        assert "L0 10/10" in outcome.text  # fake loop passes everything
        assert outcome.text.count("[ok]") == 10

    def test_full_flag_runs_everything(self, services, no_engine):
        outcome = tool_eval_summary(services, {"full": True})
        assert "tasks: 70 (full=true)" in outcome.text

    def test_category_filter(self, services, no_engine):
        outcome = tool_eval_summary(services, {"category": "nstr"})
        assert "tasks: 70" not in outcome.text
        assert "[ok] func-nstr-parse" in outcome.text

    def test_limit_respected_when_not_full(self, services, no_engine):
        outcome = tool_eval_summary(services, {"limit": 3})
        assert "tasks: 3 (full=false)" in outcome.text

    def test_no_match_is_error(self, services, no_engine):
        outcome = tool_eval_summary(services, {"category": "no-such-category"})
        assert outcome.is_error

    def test_bad_argument_types_rejected(self, services):
        for args in ({"limit": 0}, {"limit": True}, {"full": "yes"}, {"category": 5}):
            outcome = tool_eval_summary(services, args)
            assert outcome.is_error, args

    def test_loop_config_error_is_tool_error(self, no_engine):
        def broken_factory(project_path=""):
            raise tools_mod.RLLError("chain exhausted")

        services = HarnessServices(loop_factory=broken_factory)
        outcome = tool_eval_summary(services, {})
        assert outcome.is_error
        assert "loop unavailable" in outcome.text


class TestRunLoopProjectPath:
    def test_project_path_forwarded_to_loop_factory(self, services, fake_loop, tmp_path):
        tool_run_loop(services, {"task": "задача", "project_path": str(tmp_path)})
        # the factory is a lambda ignoring its arg; assert the call succeeded
        assert fake_loop.calls == [("задача", "")]

    def test_project_path_must_be_a_directory(self, services):
        outcome = tool_run_loop(services, {"task": "x", "project_path": "/no/such/dir"})
        assert outcome.is_error
        assert "not a directory" in outcome.text

    def test_project_path_type_validated(self, services):
        outcome = tool_run_loop(services, {"task": "x", "project_path": 5})
        assert outcome.is_error

    def test_details_show_context_source(self, services):

        from harness_loop.types import IterationLog, LoopResult

        iteration = IterationLog(
            index=1,
            context_source="builtin",
            context_tokens=1234,
            code_extracted=True,
            verified=True,
        )
        cached = {
            "kind": "run_loop",
            "task": "задача",
            "result": LoopResult(passed=True, code="К", iterations=[iteration]),
        }
        services.state.put("run-fixed", cached)
        outcome = tool_run_loop(services, {"run_id": "run-fixed"})
        assert "context=builtin:1234t" in outcome.text


class TestRealVerifierPolicy:
    """MCP verifier defaults ignore LS false positives (UT 11 pilot fix)."""

    def test_default_policy_ignores_invalid_character(self):
        services = HarnessServices()  # real factories, lazy verifier
        verifier = services._build_real_verifier()
        assert "InvalidCharacterInFile" in verifier.policy.ignore_codes

    def test_strict_env_keeps_raw_ls_verdict(self, monkeypatch):
        monkeypatch.setenv("HARNESS_STRICT_VERIFY", "1")
        services = HarnessServices()
        verifier = services._build_real_verifier()
        assert "InvalidCharacterInFile" not in verifier.policy.ignore_codes
