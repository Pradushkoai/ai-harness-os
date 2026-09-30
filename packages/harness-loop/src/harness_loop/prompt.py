"""Prompt builders for the BSL coding loop.

Two shapes only: the initial task prompt and the fix prompt that feeds
verifier diagnostics back to the model. Kept in one module so prompt
changes are reviewable separately from loop logic. All prompts are
Russian on purpose: the models in the routing chains are RU-friendly,
and so are the 1C projects this harness serves.
"""

from __future__ import annotations

from typing import Sequence

SYSTEM_PROMPT = (
    "Ты — senior-разработчик 1С. Ты пишешь код на встроенном языке 1С (BSL) "
    "для модулей конфигурации.\n\n"
    "Формат ответа — строго:\n"
    "- только код модуля, без пояснений до и после;\n"
    "- код обёрнут в блок ```bsl ... ```;\n"
    "- полный текст модуля, а не фрагмент или diff.\n\n"
    "Требования к коду:\n"
    "- корректный синтаксис BSL (Процедура…КонецПроцедуры, "
    "Функция…КонецФункции, Если…Тогда…КонецЕсли);\n"
    "- идентификаторы и строковые литералы — на русском языке;\n"
    "- код должен проходить статический анализ bsl-language-server "
    "без диагностик уровня Error;\n"
    "- не используй устаревшие методы (например, Сообщить()) — применяй "
    "актуальные аналоги, если задача явно не требует иного."
)


def task_prompt(task: str, context: str = "") -> str:
    """Initial user prompt: the task (+ optional project context)."""

    parts = [f"Задача:\n{task.strip()}"]
    if context.strip():
        parts.append(f"Контекст проекта:\n{context.strip()}")
    parts.append("Напиши модуль. Ответ — только код в блоке ```bsl ... ```.")
    return "\n\n".join(parts)


def fix_prompt(
    task: str,
    context: str,
    code: str,
    diagnostics: Sequence[str],
) -> str:
    """Follow-up prompt: previous code + verifier diagnostics -> fixed code."""

    parts = [f"Задача:\n{task.strip()}"]
    if context.strip():
        parts.append(f"Контекст проекта:\n{context.strip()}")
    parts.append(
        "Предыдущая версия модуля не прошла статическую проверку "
        "(bsl-language-server)."
    )
    parts.append(f"Предыдущий код:\n```bsl\n{code.rstrip()}\n```")
    shown = "\n".join(diagnostics) if diagnostics else "(диагностик нет)"
    parts.append("Диагностики верификатора (строки и колонки — 1-based):\n" + shown)
    parts.append(
        "Исправь модуль так, чтобы ушли все ошибки уровня Error. "
        "Ответ — только исправленный полный код в блоке ```bsl ... ```."
    )
    return "\n\n".join(parts)
