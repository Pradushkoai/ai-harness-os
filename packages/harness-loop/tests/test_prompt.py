"""Tests for prompt builders."""

from __future__ import annotations

from harness_loop.prompt import (
    JUDGE_REFERENCE_SYSTEM_PROMPT,
    JUDGE_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    fix_prompt,
    judge_review_prompt,
    task_prompt,
)


class TestSystemPrompt:
    def test_mentions_bsl_fence(self):
        assert "```bsl" in SYSTEM_PROMPT

    def test_mentions_verifier(self):
        assert "bsl-language-server" in SYSTEM_PROMPT


class TestTaskPrompt:
    def test_contains_task(self):
        prompt = task_prompt("Напиши функцию суммы")
        assert "Напиши функцию суммы" in prompt
        assert "```bsl" in prompt

    def test_with_context(self):
        prompt = task_prompt("задача", context="Конфигурация УТ 11.5")
        assert "Конфигурация УТ 11.5" in prompt
        assert "Контекст проекта" in prompt

    def test_without_context_no_section(self):
        assert "Контекст проекта" not in task_prompt("задача")


class TestFixPrompt:
    def test_contains_code_and_diagnostics(self):
        prompt = fix_prompt(
            task="задача",
            context="",
            code="Процедура А()\nКонецПроцедуры",
            diagnostics=["module.bsl:4:1 ERROR ParseError: Ошибка разбора"],
        )
        assert "Процедура А()" in prompt
        assert "module.bsl:4:1 ERROR ParseError: Ошибка разбора" in prompt
        assert "задача" in prompt

    def test_context_included_when_given(self):
        prompt = fix_prompt("задача", "контекст проекта", "код", ["diag"])
        assert "контекст проекта" in prompt

    def test_empty_diagnostics_renders_placeholder(self):
        prompt = fix_prompt("задача", "", "код", [])
        assert "(диагностик нет)" in prompt


class TestJudgeReviewPrompt:
    REFERENCE = "Функция Сумма(А, Б)\n    Возврат А + Б;\nКонецФункции"

    def test_with_reference_carries_etalon_section(self):
        prompt = judge_review_prompt("задача", "Код", (), reference=self.REFERENCE)
        assert "Эталонное решение" in prompt
        assert self.REFERENCE in prompt
        assert "сравнивай семантику" in prompt

    def test_without_reference_has_no_etalon_section(self):
        prompt = judge_review_prompt("задача", "Код", ())
        assert "Эталонное решение" not in prompt
        assert "сравнивай семантику" not in prompt

    def test_blank_reference_treated_as_absent(self):
        prompt = judge_review_prompt("задача", "Код", (), reference="   \n")
        assert "Эталонное решение" not in prompt

    def test_reference_keeps_task_and_code_and_diagnostics(self):
        prompt = judge_review_prompt(
            "задача X", "Код модуля", ("d1",), reference=self.REFERENCE
        )
        assert "задача X" in prompt
        assert "Код модуля" in prompt
        assert "d1" in prompt

    def test_reference_mode_instructions_present(self):
        prompt = judge_review_prompt("задача", "Код", (), reference=self.REFERENCE)
        assert "Сравни модуль с эталоном" in prompt


class TestJudgeSystemPrompts:
    def test_reference_prompt_differs_from_plain(self):
        assert JUDGE_REFERENCE_SYSTEM_PROMPT != JUDGE_SYSTEM_PROMPT

    def test_reference_prompt_mentions_etalon_semantics(self):
        assert "Эталон" in JUDGE_REFERENCE_SYSTEM_PROMPT
        assert "семантик" in JUDGE_REFERENCE_SYSTEM_PROMPT.lower()
        assert "off-by-one" in JUDGE_REFERENCE_SYSTEM_PROMPT

    def test_plain_prompt_has_no_etalon_instructions(self):
        assert "Эталон" not in JUDGE_SYSTEM_PROMPT

    def test_both_prompts_demand_strict_format(self):
        for prompt in (JUDGE_SYSTEM_PROMPT, JUDGE_REFERENCE_SYSTEM_PROMPT):
            assert "VERDICT: PASS или FAIL" in prompt
            assert "SCORE: <целое 0-10>" in prompt
