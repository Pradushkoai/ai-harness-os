"""Loop+judge integration: veto rounds, judge_error semantics (fakes only)."""

from __future__ import annotations

from russian_llm_pack import RLLError

from bsl_verify import Severity

from harness_loop.judge import Judge
from harness_loop.loop import BslAgentLoop
from harness_loop.types import LoopConfig

from conftest import MODULE_OK, FakeLLMPort, FakeVerifier, fenced, make_verify_result

PASS = "VERDICT: PASS\nSCORE: 9\nREASONING: ok"
FAIL = (
    "VERDICT: FAIL\nSCORE: 2\nISSUES:\n- пустой массив\n- нет проверки\n"
    "REASONING: задача решена частично."
)


def make_ok():
    return make_verify_result(passed=True)


class TestJudgeApproves:
    def test_passed_with_verdict(self):
        llm = FakeLLMPort([fenced(MODULE_OK)])
        verifier = FakeVerifier([make_ok()])
        judge = Judge(FakeLLMPort([PASS]))
        loop = BslAgentLoop(llm, verifier, LoopConfig(), judge=judge)

        result = loop.run("задача")

        assert result.passed is True
        assert result.judge is not None and result.judge.approved is True
        assert result.judge.score == 9
        assert result.judge_error is None
        assert result.iterations[0].judge_verdict is True

    def test_summary_mentions_judge(self):
        llm = FakeLLMPort([fenced(MODULE_OK)])
        verifier = FakeVerifier([make_ok()])
        judge = Judge(FakeLLMPort([PASS]))
        result = BslAgentLoop(llm, verifier, LoopConfig(), judge=judge).run("задача")

        lines = "\n".join(result.summary_lines())
        assert "judge PASS" in lines
        assert "judge: PASS" in lines

    def test_json_contains_judge_block(self):
        llm = FakeLLMPort([fenced(MODULE_OK)])
        verifier = FakeVerifier([make_ok()])
        judge = Judge(FakeLLMPort([PASS]))
        result = BslAgentLoop(llm, verifier, LoopConfig(), judge=judge).run("задача")

        payload = result.to_dict()
        assert payload["judge"]["approved"] is True
        assert payload["judge"]["score"] == 9
        assert payload["totals"]["judge_ms"] == 12.5
        assert payload["iterations"][0]["judge_verdict"] is True


class TestJudgeVeto:
    def test_veto_triggers_judge_fix_round_then_success(self):
        llm = FakeLLMPort([fenced(MODULE_OK), fenced(MODULE_OK)])
        verifier = FakeVerifier([make_ok(), make_ok()])
        judge = Judge(FakeLLMPort([FAIL, PASS]))
        loop = BslAgentLoop(llm, verifier, LoopConfig(max_iterations=3), judge=judge)

        result = loop.run("задача")

        assert result.passed is True
        assert result.judge.approved is True
        assert len(result.iterations) == 2
        assert result.iterations[0].judge_verdict is False
        assert result.iterations[1].judge_verdict is True

        # the second generation call used the judge fix prompt
        second_user = llm.calls[1][1].content
        assert "ревьюер его отклонил" in second_user
        assert "- пустой массив" in second_user
        # the judge saw the residual verifier diagnostics of its round
        judge_user = judge._llm.calls[0][1].content
        assert "задача" in judge_user

    def test_veto_survives_budget_is_judge_rejected(self):
        llm = FakeLLMPort([fenced(MODULE_OK)] * 3)
        verifier = FakeVerifier([make_ok()] * 3)
        judge = Judge(FakeLLMPort([FAIL] * 3))
        loop = BslAgentLoop(llm, verifier, LoopConfig(max_iterations=3), judge=judge)

        result = loop.run("задача")

        assert result.passed is False
        assert result.failure_reason == "judge_rejected"
        # v0.5: the LAST verdict survives vetoes so reports keep L2 data
        assert result.judge is not None and result.judge.approved is False
        assert result.judge.score == 2
        assert result.iterations[-1].judge_verdict is False

    def test_budget_exhausted_by_verifier_keeps_old_reason(self):
        broken = make_verify_result(
            passed=False, diagnostics=[("ParseError", Severity.ERROR, 2)]
        )
        llm = FakeLLMPort([fenced(MODULE_OK)] * 3)
        verifier = FakeVerifier([broken] * 3)
        judge = Judge(FakeLLMPort([]))  # never called: verifier never passes
        loop = BslAgentLoop(llm, verifier, LoopConfig(max_iterations=3), judge=judge)

        result = loop.run("задача")

        assert result.passed is False
        assert result.failure_reason == "budget_exhausted"
        assert all(i.judge_verdict is None for i in result.iterations)

    def test_verifier_failure_after_veto_uses_verifier_prompt(self):
        broken = make_verify_result(
            passed=False, diagnostics=[("ParseError", Severity.ERROR, 2)]
        )
        llm = FakeLLMPort([fenced(MODULE_OK), fenced(MODULE_OK), fenced(MODULE_OK)])
        verifier = FakeVerifier([make_ok(), broken, make_ok()])
        judge = Judge(FakeLLMPort([FAIL, PASS]))
        loop = BslAgentLoop(llm, verifier, LoopConfig(max_iterations=3), judge=judge)

        result = loop.run("задача")

        assert result.passed is True
        # iteration 2 prompt: judge issues (veto happened in iteration 1)
        second_user = llm.calls[1][1].content
        assert "ревьюер его отклонил" in second_user
        # iteration 3 prompt: verifier diagnostics (verifier failed in iteration 2)
        third_user = llm.calls[2][1].content
        assert "не прошла статическую проверку" in third_user
        assert "ревьюер его отклонил" not in third_user


class TestJudgeOutage:
    def test_judge_rllerror_keeps_verifier_pass(self):
        llm = FakeLLMPort([fenced(MODULE_OK)])

        class DeadJudgeLLM(FakeLLMPort):
            def complete(self, messages, *, model=None, **kwargs):
                raise RLLError("judge chain exhausted")

        verifier = FakeVerifier([make_ok()])
        judge = Judge(DeadJudgeLLM([]))
        loop = BslAgentLoop(llm, verifier, LoopConfig(), judge=judge)

        result = loop.run("задача")

        assert result.passed is True  # verifier stays the authority
        assert result.judge is None
        assert result.judge_error == "judge chain exhausted"
        lines = "\n".join(result.summary_lines())
        assert "judge unavailable" in lines

    def test_json_surfaces_judge_error(self):
        llm = FakeLLMPort([fenced(MODULE_OK)])

        class DeadJudgeLLM(FakeLLMPort):
            def complete(self, messages, *, model=None, **kwargs):
                raise RLLError("no keys")

        verifier = FakeVerifier([make_ok()])
        judge = Judge(DeadJudgeLLM([]))
        result = BslAgentLoop(llm, verifier, LoopConfig(), judge=judge).run("задача")

        payload = result.to_dict()
        assert payload["passed"] is True
        assert payload["judge_error"] == "no keys"
        assert payload["judge"] is None


class TestReferenceAwareLoop:
    """run(reference=...): the etalon reaches the judge and ONLY the judge."""

    REFERENCE = (
        "Функция Эталонная(А, Б)\n"
        "    Возврат А + Б;\n"
        "КонецФункции"
    )
    MARKER = "Эталонная"

    def test_reference_reaches_judge_not_generator(self):
        llm = FakeLLMPort([fenced(MODULE_OK)])
        verifier = FakeVerifier([make_ok()])
        judge_llm = FakeLLMPort([PASS])
        judge = Judge(judge_llm)
        loop = BslAgentLoop(llm, verifier, LoopConfig(), judge=judge)

        result = loop.run("задача", reference=self.REFERENCE)

        assert result.passed is True
        # the judge saw the etalon...
        judge_user = judge_llm.calls[0][1].content
        assert self.REFERENCE in judge_user
        judge_system = judge_llm.calls[0][0].content
        assert "Эталон" in judge_system
        # ...the generator did not (anti-cheating invariant)
        for messages in llm.calls:
            for message in messages:
                assert self.MARKER not in message.content

    def test_reference_absent_from_veto_fix_prompt(self):
        """After a veto the judge-fix prompt still hides the etalon."""
        llm = FakeLLMPort([fenced(MODULE_OK), fenced(MODULE_OK)])
        verifier = FakeVerifier([make_ok(), make_ok()])
        judge = Judge(FakeLLMPort([FAIL, PASS]))
        loop = BslAgentLoop(llm, verifier, LoopConfig(max_iterations=3), judge=judge)

        result = loop.run("задача", reference=self.REFERENCE)

        assert result.passed is True
        second_user = llm.calls[1][1].content
        assert "ревьюер его отклонил" in second_user  # it IS a judge-fix round
        assert self.MARKER not in second_user         # ...without the etalon

    def test_no_reference_backward_compatible(self):
        llm = FakeLLMPort([fenced(MODULE_OK)])
        verifier = FakeVerifier([make_ok()])
        judge_llm = FakeLLMPort([PASS])
        judge = Judge(judge_llm)
        loop = BslAgentLoop(llm, verifier, LoopConfig(), judge=judge)

        result = loop.run("задача")

        assert result.passed is True
        assert "Эталонное решение" not in judge_llm.calls[0][1].content

    def test_reference_without_judge_is_ignored(self):
        """No judge configured: the reference is simply unused, no error."""
        llm = FakeLLMPort([fenced(MODULE_OK)])
        verifier = FakeVerifier([make_ok()])
        loop = BslAgentLoop(llm, verifier, LoopConfig())

        result = loop.run("задача", reference=self.REFERENCE)

        assert result.passed is True
        for messages in llm.calls:
            for message in messages:
                assert self.MARKER not in message.content


class TestNoCodeAfterVeto:
    def test_no_code_round_resets_to_verifier_prompt(self):
        llm = FakeLLMPort([fenced(MODULE_OK), "отказываюсь без кода", fenced(MODULE_OK)])
        verifier = FakeVerifier([make_ok(), make_ok()])
        judge = Judge(FakeLLMPort([FAIL, PASS]))
        loop = BslAgentLoop(llm, verifier, LoopConfig(max_iterations=3), judge=judge)

        result = loop.run("задача")

        assert result.passed is True
        # iteration 3 prompt: plain fix prompt (answer shape problem)
        third_user = llm.calls[2][1].content
        assert "```bsl" in third_user  # asks to return fenced code
        assert "ревьюер" not in third_user
