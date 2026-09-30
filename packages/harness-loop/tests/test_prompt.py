"""Tests for prompt builders."""

from __future__ import annotations

from harness_loop.prompt import SYSTEM_PROMPT, fix_prompt, task_prompt


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
