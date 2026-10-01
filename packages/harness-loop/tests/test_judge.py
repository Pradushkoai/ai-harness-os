"""Judge tests: tolerant parser, review flow, feedback shaping (fakes only)."""

from __future__ import annotations

import pytest
from russian_llm_pack import RLLError

from harness_loop.judge import (
    Judge,
    JudgeConfig,
    JudgeVerdict,
    judge_feedback,
    parse_judge_response,
)
from harness_loop.prompt import (
    JUDGE_REFERENCE_SYSTEM_PROMPT,
    JUDGE_SYSTEM_PROMPT,
    judge_fix_prompt,
    judge_review_prompt,
)

from conftest import MODULE_OK, FakeLLMPort

PASS_TEXT = (
    "VERDICT: PASS\n"
    "SCORE: 8\n"
    "ISSUES:\n"
    "- имя функции слишком длинное\n"
    "REASONING: модуль решает задачу, стиль приемлемый."
)

FAIL_TEXT = (
    "VERDICT: FAIL\n"
    "SCORE: 3\n"
    "ISSUES:\n"
    "- нет обработки пустого массива\n"
    "- лишняя переменная\n"
    "- цикл не завершается\n"
    "REASONING: модуль не решает задачу в общем случае."
)


class TestParseVerdict:
    def test_clean_pass(self):
        v = parse_judge_response(PASS_TEXT)
        assert v.approved is True
        assert v.parsed is True
        assert v.score == 8
        assert v.issues == ("имя функции слишком длинное",)
        assert "приемлемый" in v.reasoning

    def test_clean_fail(self):
        v = parse_judge_response(FAIL_TEXT)
        assert v.approved is False
        assert v.score == 3
        assert len(v.issues) == 3
        assert v.issues[0] == "нет обработки пустого массива"

    def test_russian_section_names(self):
        text = (
            "ВЕРДИКТ: ОТКЛОНЕНО\nОЦЕНКА: 2\nПРОБЛЕМЫ:\n- критический дефект\n"
            "ОБОСНОВАНИЕ: модуль падает на пустом вводе."
        )
        v = parse_judge_response(text)
        assert v.approved is False
        assert v.score == 2
        assert v.issues == ("критический дефект",)
        assert "падает" in v.reasoning

    def test_ru_pass_verdict(self):
        v = parse_judge_response("ВЕРДИКТ: ПРОЙДЕНО\nSCORE: 9\n")
        assert v.approved is True
        assert v.score == 9

    def test_no_verdict_no_fail_words_passes_unparsed(self):
        v = parse_judge_response("Модуль выглядит хорошо, замечаний нет.")
        assert v.approved is True
        assert v.parsed is False

    def test_fail_in_prose_without_format_still_vetoes(self):
        v = parse_judge_response("По моему мнению, решение отклонено: логика ошибочна.")
        assert v.approved is False
        assert v.parsed is False

    def test_fail_words_win_over_pass_words(self):
        v = parse_judge_response("VERDICT: НЕ ПРОЙДЕНО\n")
        assert v.approved is False

    def test_score_clamped_to_ten(self):
        v = parse_judge_response("VERDICT: PASS\nSCORE: 99\n")
        assert v.score == 10

    def test_no_score_is_none(self):
        v = parse_judge_response("VERDICT: PASS\n")
        assert v.score is None

    def test_issues_capped(self):
        issues = "\n".join(f"- проблема {i}" for i in range(15))
        text = f"VERDICT: FAIL\nSCORE: 1\nISSUES:\n{issues}\nREASONING: много проблем."
        v = parse_judge_response(text, max_issues=5)
        assert len(v.issues) == 5

    def test_numbered_bullets(self):
        text = "VERDICT: FAIL\nISSUES:\n1. первое\n2. второе\n"
        v = parse_judge_response(text)
        assert v.issues == ("первое", "второе")

    def test_plain_line_under_issues_counts(self):
        text = "VERDICT: FAIL\nISSUES:\nмодуль не компилируется\n"
        v = parse_judge_response(text)
        assert v.issues == ("модуль не компилируется",)

    def test_fail_without_issues_gets_reasoning_note(self):
        v = parse_judge_response("VERDICT: FAIL\nREASONING: задача не решена.")
        assert v.approved is False
        assert v.issues == ()
        assert "не решена" in v.reasoning

    def test_max_issues_config_validation(self):
        with pytest.raises(ValueError):
            JudgeConfig(max_issues=0)


class TestJudgeReview:
    def test_review_passes_prompt_and_parses(self):
        llm = FakeLLMPort([PASS_TEXT])
        judge = Judge(llm)
        verdict = judge.review("Напиши функцию суммы", MODULE_OK, ["w: стиль"])

        assert verdict.approved is True
        assert verdict.model == "fake-model"
        assert verdict.latency_ms == 12.5
        system, user = llm.calls[0]
        assert JUDGE_SYSTEM_PROMPT in system.content or "ревьюер" in system.content
        assert "Напиши функцию суммы" in user.content
        assert MODULE_OK.strip() in user.content
        assert "w: стиль" in user.content

    def test_review_rll_error_propagates(self):
        class NoModel(FakeLLMPort):
            def complete(self, messages, *, model=None, **kwargs):
                raise RLLError("judge chain exhausted")

        judge = Judge(NoModel([]))
        with pytest.raises(RLLError):
            judge.review("задача", MODULE_OK)

    def test_review_forwards_config_kwargs(self):
        llm = FakeLLMPort([PASS_TEXT])
        judge = Judge(llm, JudgeConfig(temperature=0.2, max_tokens=512))
        judge.review("задача", MODULE_OK)
        assert llm.kwargs[0] == {"temperature": 0.2, "max_tokens": 512}

    def test_config_defaults_send_no_kwargs(self):
        llm = FakeLLMPort([PASS_TEXT])
        Judge(llm).review("задача", MODULE_OK)
        assert llm.kwargs[0] == {}


class TestReferenceAwareReview:
    REFERENCE = "Функция Сумма(А, Б)\n    Возврат А + Б;\nКонецФункции"

    def test_reference_switches_system_prompt(self):
        llm = FakeLLMPort([PASS_TEXT])
        judge = Judge(llm)
        judge.review("задача", MODULE_OK, reference=self.REFERENCE)

        system = llm.calls[0][0].content
        assert system == JUDGE_REFERENCE_SYSTEM_PROMPT

    def test_reference_reaches_user_prompt(self):
        llm = FakeLLMPort([PASS_TEXT])
        judge = Judge(llm)
        judge.review("задача", MODULE_OK, reference=self.REFERENCE)

        user = llm.calls[0][1].content
        assert self.REFERENCE in user
        assert "Эталонное решение" in user

    def test_no_reference_keeps_plain_system_prompt(self):
        llm = FakeLLMPort([PASS_TEXT])
        judge = Judge(llm)
        judge.review("задача", MODULE_OK)

        system = llm.calls[0][0].content
        assert system == JUDGE_SYSTEM_PROMPT
        assert "Эталонное решение" not in llm.calls[0][1].content

    def test_blank_reference_keeps_plain_system_prompt(self):
        llm = FakeLLMPort([PASS_TEXT])
        judge = Judge(llm)
        judge.review("задача", MODULE_OK, reference=" \n")

        assert llm.calls[0][0].content == JUDGE_SYSTEM_PROMPT

    def test_reference_review_still_parses_verdict(self):
        judge = Judge(FakeLLMPort([FAIL_TEXT]))
        verdict = judge.review("задача", MODULE_OK, reference=self.REFERENCE)

        assert verdict.approved is False
        assert verdict.score == 3
        assert verdict.issues


class TestJudgeFeedback:
    def test_issues_become_feedback(self):
        verdict = JudgeVerdict(approved=False, issues=("проблема а", "проблема б"))
        assert judge_feedback(verdict) == ["проблема а", "проблема б"]

    def test_reasoning_when_no_issues(self):
        verdict = JudgeVerdict(approved=False, reasoning="слишком наивно")
        assert judge_feedback(verdict) == ["Обоснование ревьюера: слишком наивно"]

    def test_fallback_line(self):
        verdict = JudgeVerdict(approved=False)
        assert judge_feedback(verdict) == [
            "ревьюер отклонил модуль без объяснений"
        ]


class TestJudgePrompts:
    def test_review_prompt_shape(self):
        prompt = judge_review_prompt("задача X", "Код", ("d1", "d2"))
        assert "задача X" in prompt
        assert "```bsl" in prompt
        assert "d1" in prompt and "d2" in prompt

    def test_review_prompt_empty_diagnostics(self):
        assert "(нет)" in judge_review_prompt("з", "К")

    def test_fix_prompt_shape(self):
        prompt = judge_fix_prompt("задача X", "контекст", "Код", ("i1",))
        assert "ревьюер его отклонил" in prompt
        assert "- i1" in prompt
        assert "контекст" in prompt

    def test_fix_prompt_no_issues_placeholder(self):
        assert "(замечаний нет)" in judge_fix_prompt("з", "", "К", ())
