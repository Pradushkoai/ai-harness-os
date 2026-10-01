"""Prompt builders for the BSL coding loop.

Three shapes: the initial task prompt, the fix prompt that feeds verifier
diagnostics back to the model, and the judge prompts (review + judge-fix).
Kept in one module so prompt changes are reviewable separately from loop
logic. All prompts are Russian on purpose: the models in the routing
chains are RU-friendly, and so are the 1C projects this harness serves.
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


# -- judge (second opinion) ---------------------------------------------------------


JUDGE_SYSTEM_PROMPT = (
    "Ты — строгий ревьюер BSL-кода. Оцени, решает ли модуль поставленную "
    "задачу и нет ли дефектов, которые пропустил статический анализатор.\n\n"
    "Формат ответа — строго:\n"
    "VERDICT: PASS или FAIL\n"
    "SCORE: <целое 0-10>\n"
    "ISSUES:\n"
    "- <замечание>\n"
    "REASONING: <одно-два предложения>\n\n"
    "Правила:\n"
    "- PASS — только если модуль решает задачу и без существенных дефектов;\n"
    "- замечания — краткие и по делу, максимум 10;\n"
    "- не требуй рефакторинга ради рефакторинга."
)


def judge_review_prompt(
    task: str,
    code: str,
    diagnostics: Sequence[str] = (),
) -> str:
    """Judge input: original task + generated module + residual diagnostics."""

    parts = [f"Задача, которую должен решать модуль:\n{task.strip()}"]
    parts.append(f"Код модуля:\n```bsl\n{code.rstrip()}\n```")
    shown = "\n".join(diagnostics) if diagnostics else "(нет)"
    parts.append(
        "Остаточные диагностики статического анализатора (политику прошёл):\n"
        + shown
    )
    parts.append("Проанализируй модуль и вынеси вердикт в указанном формате.")
    return "\n\n".join(parts)


def judge_fix_prompt(
    task: str,
    context: str,
    code: str,
    issues: Sequence[str],
) -> str:
    """Follow-up prompt after a judge rejection: review issues -> fixed code."""

    parts = [f"Задача:\n{task.strip()}"]
    if context.strip():
        parts.append(f"Контекст проекта:\n{context.strip()}")
    parts.append(
        "Модуль прошёл статическую проверку, но ревьюер его отклонил."
    )
    parts.append(f"Предыдущий код:\n```bsl\n{code.rstrip()}\n```")
    shown = "\n".join(f"- {issue}" for issue in issues) or "(замечаний нет)"
    parts.append("Замечания ревьюера:\n" + shown)
    parts.append(
        "Исправь модуль с учётом замечаний ревьюера. "
        "Ответ — только исправленный полный код в блоке ```bsl ... ```."
    )
    return "\n\n".join(parts)
