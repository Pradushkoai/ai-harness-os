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

JUDGE_REFERENCE_SYSTEM_PROMPT = (
    "Ты — строгий ревьюер BSL-кода. Тебе даны: задача, эталонное решение, "
    "модуль кандидата и остаточные диагностики статического анализатора.\n\n"
    "Эталон — один из возможных корректных вариантов, а не единственный: "
    "другой стиль, другие имена переменных или другой обход коллекции "
    "НЕ являются дефектом. Сравнивай семантику решения с эталоном:\n"
    "- та ли формула вычисления, те ли операции и в том ли порядке;\n"
    "- границы условий (включительно/исключительно), off-by-one;\n"
    "- поведение на граничных и вырожденных случаях (пустой ввод, ноль, "
    "отрицательные значения, один элемент);\n"
    "- тип и структура возвращаемого результата.\n\n"
    "FAIL — если семантика модуля отличается от эталонной или задача решена "
    "не полностью, даже если статический анализ чист. PASS — если модуль "
    "решает ту же задачу той же семантикой (пусть и другим способом).\n\n"
    "Формат ответа — строго:\n"
    "VERDICT: PASS или FAIL\n"
    "SCORE: <целое 0-10>\n"
    "ISSUES:\n"
    "- <замечание>\n"
    "REASONING: <одно-два предложения>\n\n"
    "Правила:\n"
    "- в замечаниях указывай конкретное семантическое расхождение, "
    "а не разницу в стиле;\n"
    "- замечания — краткие и по делу, максимум 10;\n"
    "- не требуй рефакторинга ради рефакторинга."
)


def judge_review_prompt(
    task: str,
    code: str,
    diagnostics: Sequence[str] = (),
    reference: str = "",
) -> str:
    """Judge input: task + generated module + residual diagnostics (+ reference).

    With a non-empty `reference` the prompt carries an explicit etalon
    section and comparison instructions — the reference-aware judge mode
    (SWE-bench-BSL v0.4 judge protocol).
    """

    parts = [f"Задача, которую должен решать модуль:\n{task.strip()}"]
    if reference.strip():
        parts.append(
            "Эталонное решение (один из возможных корректных вариантов — "
            "сравнивай семантику, а не стиль):\n"
            f"```bsl\n{reference.rstrip()}\n```"
        )
    parts.append(f"Код модуля:\n```bsl\n{code.rstrip()}\n```")
    shown = "\n".join(diagnostics) if diagnostics else "(нет)"
    parts.append(
        "Остаточные диагностики статического анализатора (политику прошёл):\n"
        + shown
    )
    if reference.strip():
        parts.append(
            "Сравни модуль с эталоном: семантика, формулы, границы условий, "
            "граничные случаи, возвращаемый результат. Вынеси вердикт "
            "в указанном формате."
        )
    else:
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
