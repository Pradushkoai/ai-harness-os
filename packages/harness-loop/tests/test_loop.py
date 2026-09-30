"""Tests for BslAgentLoop: generate -> verify -> fix, all on fakes.

The loop's contract under test:
    - success stops the loop immediately;
    - verifier diagnostics are fed back into the NEXT LLM call;
    - budget exhaustion, LLM errors and verifier env errors are reported,
      never raised;
    - every iteration is inspectable in LoopResult.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from russian_llm_pack.types import CompletionResult, RLLError, UsageInfo

from harness_loop.loop import BslAgentLoop, RouterPort, format_diagnostics
from harness_loop.types import LoopConfig

from conftest import MODULE_BROKEN, MODULE_OK, FakeLLMPort, FakeVerifier, fenced, make_verify_result


def make_loop(responses, results, config=None):
    llm = FakeLLMPort(responses)
    verifier = FakeVerifier(results)
    loop = BslAgentLoop(llm=llm, verifier=verifier, config=config)
    return loop, llm, verifier


class TestHappyPath:
    def test_success_on_first_iteration(self, ok_result):
        loop, llm, verifier = make_loop([fenced(MODULE_OK)], [ok_result])

        result = loop.run("Напиши процедуру")

        assert result.passed is True
        assert result.failure_reason == ""
        assert result.code == MODULE_OK
        assert len(result.iterations) == 1
        assert result.iterations[0].verified is True
        assert result.iterations[0].code_extracted is True
        assert verifier.seen_texts == [MODULE_OK]
        assert len(llm.calls) == 1  # loop stopped, no extra LLM calls

    def test_first_call_uses_task_prompt_with_system(self, ok_result):
        loop, llm, _ = make_loop([fenced(MODULE_OK)], [ok_result])

        loop.run("Напиши функцию Сумма")

        first = llm.calls[0]
        assert first[0].role == "system"
        assert "```bsl" in first[0].content
        assert "Напиши функцию Сумма" in first[1].content
        assert first[1].role == "user"

    def test_token_and_latency_bookkeeping(self, ok_result):
        loop, _, _ = make_loop([fenced(MODULE_OK)], [ok_result])

        result = loop.run("задача")

        assert result.total_prompt_tokens == 100
        assert result.total_completion_tokens == 50
        assert result.iterations[0].llm_latency_ms == 12.5
        assert result.iterations[0].model == "fake-model"


class TestFixFlow:
    def test_success_on_second_iteration_feeds_diagnostics_back(self):
        from bsl_verify.types import Severity

        broken_result = make_verify_result(
            passed=False, diagnostics=[("ParseError", Severity.ERROR, 4)]
        )
        ok_result = make_verify_result(passed=True)
        loop, llm, verifier = make_loop(
            [fenced(MODULE_BROKEN), fenced(MODULE_OK)],
            [broken_result, ok_result],
        )

        result = loop.run("Напиши процедуру")

        assert result.passed is True
        assert len(result.iterations) == 2
        assert result.iterations[0].errors == 1
        assert result.iterations[0].verified is False

        # THE backpressure wiring: iteration-1 diagnostics land in call 2
        second_user = llm.calls[1][1].content
        assert "ParseError" in second_user
        assert "СломаннаяПроцедура" in second_user  # previous code included
        assert "Напиши процедуру" in second_user  # task restated

        # and the verifier saw exactly the two extracted versions
        assert verifier.seen_texts[0] == MODULE_BROKEN
        assert verifier.seen_texts[1] == MODULE_OK

    def test_context_reaches_both_prompts(self):
        from bsl_verify.types import Severity

        broken_result = make_verify_result(
            passed=False, diagnostics=[("ParseError", Severity.ERROR, 4)]
        )
        ok_result = make_verify_result(passed=True)
        loop, llm, _ = make_loop(
            [fenced(MODULE_BROKEN), fenced(MODULE_OK)],
            [broken_result, ok_result],
        )

        loop.run("задача", context="Конфигурация УТ 11.5, модуль заказов")

        assert "Конфигурация УТ 11.5" in llm.calls[0][1].content
        assert "Конфигурация УТ 11.5" in llm.calls[1][1].content

    def test_warnings_do_not_fail_but_are_reported(self):
        from bsl_verify.types import Severity

        noisy_ok = make_verify_result(
            passed=True,
            diagnostics=[
                ("SomeWarning", Severity.WARNING, 2),
                ("SomeInfo", Severity.INFORMATION, 3),
            ],
        )
        loop, _, _ = make_loop([fenced(MODULE_OK)], [noisy_ok])

        result = loop.run("задача")

        assert result.passed is True
        assert result.iterations[0].warnings == 1
        assert result.iterations[0].infos == 1
        assert any("SomeWarning" in line for line in result.iterations[0].diagnostics)


class TestBudget:
    def test_budget_exhausted(self):
        from bsl_verify.types import Severity

        broken = make_verify_result(
            passed=False, diagnostics=[("ParseError", Severity.ERROR, 4)]
        )
        loop, llm, _ = make_loop(
            [fenced(MODULE_BROKEN)] * 3, [broken] * 3, config=LoopConfig(max_iterations=3)
        )

        result = loop.run("задача")

        assert result.passed is False
        assert result.failure_reason == "budget_exhausted"
        assert len(result.iterations) == 3
        assert len(llm.calls) == 3
        assert result.code == MODULE_BROKEN  # last extracted version kept

    def test_single_iteration_config(self):
        broken = make_verify_result(passed=False)
        loop, llm, _ = make_loop(
            [fenced(MODULE_BROKEN)] * 5, [broken] * 5, config=LoopConfig(max_iterations=1)
        )

        result = loop.run("задача")

        assert result.passed is False
        assert len(llm.calls) == 1

    def test_invalid_config_rejected(self):
        with pytest.raises(ValueError):
            LoopConfig(max_iterations=0)
        with pytest.raises(ValueError):
            LoopConfig(max_feedback_lines=0)


class TestErrors:
    def test_llm_error_reported_not_raised(self):
        class ExplodingLLM(FakeLLMPort):
            def complete(self, messages, *, model=None, **kwargs):
                raise RLLError("no available model for task 'coding'")

        loop = BslAgentLoop(llm=ExplodingLLM([]), verifier=FakeVerifier([]))

        result = loop.run("задача")

        assert result.passed is False
        assert result.failure_reason == "llm_error"
        assert "no available model" in result.error
        assert result.iterations == []

    def test_verifier_env_error_reported_not_raised(self):
        from bsl_verify.types import BslVerifyError

        class ExplodingVerifier(FakeVerifier):
            def verify_module_text(self, text, *, filename="module.bsl"):
                raise BslVerifyError("java not found")

        loop = BslAgentLoop(llm=FakeLLMPort([fenced(MODULE_OK)]), verifier=ExplodingVerifier([]))

        result = loop.run("задача")

        assert result.passed is False
        assert result.failure_reason == "verifier_error"
        assert "java not found" in result.error
        assert len(result.iterations) == 1
        assert result.iterations[0].code_extracted is True
        assert result.iterations[0].verified is None  # never got a verdict


class TestNoCodeExtracted:
    def test_garbage_response_then_good_one(self):
        ok_result = make_verify_result(passed=True)
        loop, llm, _ = make_loop(
            ["Простите, я не понял задачу.", fenced(MODULE_OK)],
            [ok_result],
        )

        result = loop.run("задача")

        assert result.passed is True
        assert len(result.iterations) == 2
        assert result.iterations[0].code_extracted is False
        assert "```bsl" in result.iterations[0].note  # the note tells the model the format

        # the fix prompt explains the format problem
        second_user = llm.calls[1][1].content
        assert "```bsl" in second_user

    def test_only_garbage_budget_exhausted(self):
        loop, llm, verifier = make_loop(
            ["не могу", "откажусь", "в другой раз"], []
        )

        result = loop.run("задача")

        assert result.passed is False
        assert result.failure_reason == "budget_exhausted"
        assert result.code == ""
        assert verifier.seen_texts == []  # nothing ever reached the verifier
        assert len(llm.calls) == 3


class TestFeedbackShaping:
    def test_diagnostics_capped(self):
        from bsl_verify.types import Severity

        diagnostics = [(f"Code{i}", Severity.ERROR, i + 1) for i in range(40)]
        noisy = make_verify_result(passed=False, diagnostics=diagnostics)
        loop, llm, _ = make_loop(
            [fenced(MODULE_BROKEN)] * 2, [noisy, make_verify_result(passed=True)],
            config=LoopConfig(max_feedback_lines=10),
        )

        result = loop.run("задача")

        feedback = result.iterations[0].diagnostics
        assert len(feedback) == 11  # 10 lines + truncation notice
        assert "и ещё 30" in feedback[-1]
        # the fix prompt got the capped version too
        assert "и ещё 30" in llm.calls[1][1].content

    def test_errors_sorted_before_warnings_in_feedback(self):
        from bsl_verify.types import Severity

        mixed = make_verify_result(
            passed=True,
            diagnostics=[
                ("InfoCode", Severity.INFORMATION, 1),
                ("WarnCode", Severity.WARNING, 2),
                ("ErrorCode", Severity.ERROR, 3),
            ],
        )
        loop, _, _ = make_loop([fenced(MODULE_OK)], [mixed])

        result = loop.run("задача")

        lines = result.iterations[0].diagnostics
        assert "ErrorCode" in lines[0]
        assert "WarnCode" in lines[1]
        assert "InfoCode" in lines[2]

    def test_filename_used_in_feedback_lines(self):
        from bsl_verify.types import Severity

        broken = make_verify_result(
            passed=False, diagnostics=[("ParseError", Severity.ERROR, 4)]
        )
        loop, _, _ = make_loop(
            [fenced(MODULE_BROKEN)], [broken],
            config=LoopConfig(filename="МодульЗаказа.bsl", max_iterations=1),
        )

        result = loop.run("задача")

        assert "МодульЗаказа.bsl" in result.iterations[0].diagnostics[0]


class TestRouterPort:
    def test_delegates_to_router_with_pinned_task(self):
        captured = {}

        def fake_complete(task, messages, *, model=None, **kwargs):
            captured["task"] = task
            captured["model"] = model
            captured["messages"] = messages
            return CompletionResult(
                text="ok", provider="deepseek", model=model or "m",
                usage=UsageInfo(), latency_ms=1.0,
            )

        router = SimpleNamespace(
            complete=fake_complete,
            config=SimpleNamespace(default_task="coding"),
        )
        port = RouterPort(router, task="judge", model="zai/glm-4.6")
        assert port.name == "router:judge"

        result = port.complete([("user", "hi")], model="ignored-model")

        assert captured["task"] == "judge"
        assert captured["model"] == "zai/glm-4.6"  # port model wins
        assert result.model == "zai/glm-4.6"

    def test_router_model_falls_back_to_call_model(self):
        def fake_complete(task, messages, *, model=None, **kwargs):
            return CompletionResult(
                text="ok", provider="p", model=model or "m",
                usage=UsageInfo(), latency_ms=1.0,
            )

        router = SimpleNamespace(
            complete=fake_complete, config=SimpleNamespace(default_task="coding")
        )
        port = RouterPort(router, task=None)

        result = port.complete([], model="deepseek/deepseek-chat")

        assert result.model == "deepseek/deepseek-chat"


class TestConfigForwarding:
    def test_temperature_and_max_tokens_forwarded(self, ok_result):
        loop, llm, _ = make_loop(
            [fenced(MODULE_OK)], [ok_result],
            config=LoopConfig(temperature=0.7, max_tokens=512),
        )

        loop.run("задача")

        assert llm.kwargs[0] == {"temperature": 0.7, "max_tokens": 512}

    def test_no_kwargs_by_default(self, ok_result):
        loop, llm, _ = make_loop([fenced(MODULE_OK)], [ok_result])

        loop.run("задача")

        assert llm.kwargs[0] == {}


class TestCallbacks:
    def test_on_iteration_called_per_iteration(self):
        from bsl_verify.types import Severity

        broken = make_verify_result(
            passed=False, diagnostics=[("ParseError", Severity.ERROR, 4)]
        )
        ok_result = make_verify_result(passed=True)
        loop, _, _ = make_loop(
            [fenced(MODULE_BROKEN), fenced(MODULE_OK)], [broken, ok_result]
        )

        seen = []
        result = loop.run("задача", on_iteration=seen.append)

        assert [log.index for log in seen] == [1, 2]
        assert result.passed is True

    def test_broken_callback_does_not_kill_loop(self, ok_result):
        loop, _, _ = make_loop([fenced(MODULE_OK)], [ok_result])

        def bad_callback(_log):
            raise RuntimeError("callback exploded")

        result = loop.run("задача", on_iteration=bad_callback)

        assert result.passed is True


class TestResultRendering:
    def test_summary_lines(self):
        from bsl_verify.types import Severity

        broken = make_verify_result(
            passed=False, diagnostics=[("ParseError", Severity.ERROR, 4)]
        )
        loop, _, _ = make_loop(
            [fenced(MODULE_BROKEN)] * 3, [broken] * 3
        )

        result = loop.run("задача")
        lines = "\n".join(result.summary_lines())

        assert "FAILED" in lines
        assert "budget_exhausted" in lines
        assert "iter 1" in lines
        assert "verify FAILED" in lines
        assert "tokens: 300 in + 150 out" in lines

    def test_to_dict_is_json_safe(self):
        ok_result = make_verify_result(passed=True)
        loop, _, _ = make_loop([fenced(MODULE_OK)], [ok_result])

        result = loop.run("задача")
        payload = json.dumps(result.to_dict(), ensure_ascii=False)

        assert '"passed": true' in payload
        assert "ТестоваяПроцедура" in payload

    def test_format_diagnostics_plain_usage(self):
        from bsl_verify.types import Severity

        verdict = make_verify_result(
            passed=False,
            diagnostics=[("ParseError", Severity.ERROR, 4), ("W", Severity.WARNING, 2)],
        )
        lines = format_diagnostics(verdict, "module.bsl", limit=25)

        assert len(lines) == 2
        assert lines[0].startswith("module.bsl:5:1 ERROR ParseError")
        assert lines[1].startswith("module.bsl:3:1 WARNING W")
