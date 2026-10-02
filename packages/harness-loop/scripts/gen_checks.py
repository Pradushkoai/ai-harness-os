"""Generate L1 checks for the benchmark by RUNNING reference solutions.

Rule "reality > assumptions" (roadmap 2.1, step A2): expected values are
not written by hand — the reference solution is executed by the real
OneScript engine, its printed values are captured and become `expect`.
A human then reviews the captured table (this script prints it) before
the checks land in tasks_v0.yaml.

Usage (from packages/harness-loop):
    OSCRIPT_PATH=/path/to/oscript python scripts/gen_checks.py [--dry]

Round-trip gate: after generation the script re-runs every reference
against the freshly generated checks — anything that does not pass its
own checks is reported and EXCLUDED (never bake a broken expectation).

Calls inventory lives in INVENTORY below: task id -> [(call, kwargs)].
Tasks absent from the inventory keep whatever checks they already have.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from harness_loop.executors import ExecCheck, OneScriptRunner
from harness_loop.evals import load_tasks

TASKS = Path(__file__).parent.parent / "src" / "harness_loop" / "eval_data" / "tasks_v0.yaml"

# (call, kwargs) — kwargs: setup=, case_fold=, proc=
INVENTORY: dict[str, list] = {
    "func-sum-two-numbers": [
        ("СуммаДвухЧисел(2, 3)", {}),
        ("СуммаДвухЧисел(-4, 4)", {}),
    ],
    "func-max-of-two": [
        ("МаксимумИзДвух(3, 7)", {}),
        ("МаксимумИзДвух(7, 3)", {}),
        ("МаксимумИзДвух(5, 5)", {}),
    ],
    "func-is-blank-string": [
        ('ЭтоПустаяСтрока("   ")', {}),
        ('ЭтоПустаяСтрока("х")', {}),
        ('ЭтоПустаяСтрока("")', {}),
    ],
    "func-array-sum": [
        ("СуммаМассива(М)", {"setup": "М = Новый Массив; М.Добавить(1); М.Добавить(2); М.Добавить(3)"}),  # noqa: E501
        ("СуммаМассива(М)", {"setup": "М = Новый Массив"}),
    ],
    "func-discount": [
        ("РассчитатьСкидку(1000, 10)", {}),
        ("РассчитатьСкидку(1000, 0)", {}),
    ],
    "proc-numbers-1-to-10": [
        ("ВывестиЧислаОт1До10()", {"proc": True}),
    ],
    "proc-safe-division": [
        ("РазделитьЧисла(10, 2)", {"proc": True}),
        ("РазделитьЧисла(10, 0)", {"proc": True}),
    ],
    "func-phone-digits": [
        ('ТолькоЦифры("8-999-123-45-67")', {}),
        ('ТолькоЦифры("без цифр")', {}),
    ],
    "func-percent-default": [
        ("ПроцентОтЧисла(200)", {}),
        ("ПроцентОтЧисла(200, 25)", {}),
    ],
    "func-clamp": [
        ("ОграничитьЧисло(5, 1, 10)", {}),
        ("ОграничитьЧисло(0, 1, 10)", {}),
        ("ОграничитьЧисло(15, 1, 10)", {}),
    ],
    "func-factorial": [
        ("Факториал(5)", {}),
        ("Факториал(0)", {}),
        ("Факториал(-1)", {}),
    ],
    "func-fibonacci": [
        ("Фибоначчи(6)", {}),
        ("Фибоначчи(1)", {}),
        ("Фибоначчи(0)", {}),
    ],
    "func-max-in-array": [
        ("МаксимумМассива(М)", {"setup": "М = Новый Массив; М.Добавить(3); М.Добавить(9); М.Добавить(1)"}),  # noqa: E501
        ("МаксимумМассива(М)", {"setup": "М = Новый Массив; М.Добавить(-5)"}),
    ],
    "func-reverse-string": [
        ('РазвернутьСтроку("абвг")', {}),
        ('РазвернутьСтроку("")', {}),
    ],
    "func-count-char": [
        ('КоличествоВхождений("банан", "а")', {}),
        ('КоличествоВхождений("банан", "я")', {}),
    ],
    "func-capitalize": [
        ('ПерваяЗаглавная("привет")', {}),
        ('ПерваяЗаглавная("ПРИВЕТ")', {}),
        ('ПерваяЗаглавная("")', {}),
    ],
    "func-is-palindrome": [
        ('Палиндром("шалаш")', {}),
        ('Палиндром("привет")', {}),
    ],
    "func-reverse-words": [
        ('РазвернутьСлова("раз два три")', {}),
        ('РазвернутьСлова("одно")', {}),
    ],
    "func-string-template": [
        ('Приветствие("Аня", 30)', {}),
    ],
    "func-safe-parse-number": [
        ('БезопасноеЧисло("42")', {}),
        ('БезопасноеЧисло("не число")', {}),
    ],
    "func-date-diff-days": [
        ("РазницаДней(Дата(2024, 1, 1), Дата(2024, 1, 11))", {}),
    ],
    "func-leap-year": [
        ("ВисокосныйГод(2024)", {}),
        ("ВисокосныйГод(1900)", {}),
        ("ВисокосныйГод(2000)", {}),
    ],
    "func-add-months": [
        ("ДобавитьМесяцы(Дата(2024, 1, 31), 1)", {}),
        ("ДобавитьМесяцы(Дата(2024, 11, 15), 3)", {}),
    ],
    "func-element-frequencies": [
        ("ЧастотыЭлементов(М).Получить(1)", {"setup": "М = Новый Массив; М.Добавить(1); М.Добавить(2); М.Добавить(1)"}),  # noqa: E501
    ],
    "func-unique-array": [
        ("УникальныеЭлементы(М).Количество()", {"setup": "М = Новый Массив; М.Добавить(1); М.Добавить(2); М.Добавить(1)"}),  # noqa: E501
    ],
    "proc-print-mapping": [
        ("ВывестиСоответствие(Д)", {"setup": "Д = Новый Соответствие; Д.Вставить(\"а\", 1); Д.Вставить(\"б\", 2)", "proc": True}),  # noqa: E501
    ],
    "struct-nested-defaults": [
        ("НастройкиПоУмолчанию().Скидка", {}),
        ("НастройкиПоУмолчанию().Применение.Приоритет", {}),
    ],
    "func-gcd": [
        ("НОД(12, 18)", {}),
        ("НОД(7, 13)", {}),
    ],
    "func-is-prime": [
        ("ПростоееЧисло(13)", {}),
        ("ПростоееЧисло(9)", {}),
        ("ПростоееЧисло(1)", {}),
    ],
    "func-reverse-number": [
        ("РазвернутьЧисло(12345)", {}),
        ("РазвернутьЧисло(1000)", {}),
    ],
    "func-inn-10-checksum": [
        ('КорректныйИНН("7812304566")', {}),
        ('КорректныйИНН("7812304560")', {}),
        ('КорректныйИНН("123")', {}),
    ],
    "func-camel-to-snake": [
        ('ВЗмейку("моёИмяПоля")', {}),
        ('ВЗмейку("API")', {}),
    ],
    "func-csv-parse": [
        ('РазобратьСтрокуCSV("а, б ,в").Количество()', {}),
        ('РазобратьСтрокуCSV("а, б ,в")[1]', {}),
    ],
    "func-quarter-of-date": [
        ("НомерКвартала(Дата(2024, 11, 15))", {}),
        ("НомерКвартала(Дата(2024, 1, 1))", {}),
    ],
    "func-full-years": [
        ("ПолныхЛет(Дата(1990, 5, 1), Дата(2024, 5, 1))", {}),
        ("ПолныхЛет(Дата(1990, 5, 2), Дата(2024, 5, 1))", {}),
    ],
    "map-invert": [
        ('ОбратитьСоответствие(М).Получить("значение")', {"setup": 'М = Новый Соответствие; М.Вставить("ключ", "значение")'}),  # noqa: E501
    ],
    "array-chunk": [
        ("РазбитьМассив(М, 2).Количество()", {"setup": "М = Новый Массив; М.Добавить(1); М.Добавить(2); М.Добавить(3)"}),  # noqa: E501
        ("РазбитьМассив(М, 2)[1].Количество()", {"setup": "М = Новый Массив; М.Добавить(1); М.Добавить(2); М.Добавить(3)"}),  # noqa: E501
    ],
    "func-nstr-greeting": [
        ("Приветствие()", {}),
    ],
    "func-nstr-build": [
        ('СобратьНСтр("привет", "hello")', {}),
    ],
    "func-multiline-text": [
        ("МногострочныйТекст()", {}),
    ],
    "func-http-status-text": [
        ("ТекстСтатуса(200)", {}),
        ("ТекстСтатуса(404)", {}),
        ("ТекстСтатуса(999)", {}),
    ],
    "func-http-url-parts": [
        ('РазобратьАдрес("http://host:8080/path").Хост', {}),
        ('РазобратьАдрес("http://host:8080/path").Порт', {}),
    ],
    "table-column-total": [
        ('СуммаПоКолонке(Т, "Цена")', {"setup": "Т = Новый ТаблицаЗначений; Т.Колонки.Добавить(\"Цена\"); С = Т.Добавить(); С.Цена = 5; С = Т.Добавить(); С.Цена = 7"}),  # noqa: E501
    ],
    "table-find-row": [
        ('НайтиСтрокуПоКолонке(Т, "Код", 2).Наименование', {"setup": "Т = Новый ТаблицаЗначений; Т.Колонки.Добавить(\"Код\"); Т.Колонки.Добавить(\"Наименование\"); С = Т.Добавить(); С.Код = 1; С.Наименование = \"Болт\"; С = Т.Добавить(); С.Код = 2; С.Наименование = \"Гайка\""}),  # noqa: E501
    ],
    "table-filter-rows": [
        ("ОтфильтроватьПоЦене(Т, 5).Количество()", {"setup": "Т = Новый ТаблицаЗначений; Т.Колонки.Добавить(\"Код\"); Т.Колонки.Добавить(\"Цена\"); С = Т.Добавить(); С.Код = 1; С.Цена = 3; С = Т.Добавить(); С.Код = 2; С.Цена = 7"}),  # noqa: E501
    ],
    "table-sort-by-column": [
        ('ОтсортироватьТаблицу(Т, "Цена")[0].Цена', {"setup": "Т = Новый ТаблицаЗначений; Т.Колонки.Добавить(\"Цена\"); С = Т.Добавить(); С.Цена = 7; С = Т.Добавить(); С.Цена = 3"}),  # noqa: E501
    ],
    "func-tp-column-sum": [
        ('СуммаПоКолонкеТЧ(Товары, "Количество")', {"setup": "Товары = Новый ТаблицаЗначений; Товары.Колонки.Добавить(\"Количество\"); С = Товары.Добавить(); С.Количество = 3; С = Товары.Добавить(); С.Количество = 4"}),  # noqa: E501
    ],
    "func-tp-find-row": [
        ('НайтиСтрокуТЧ(Товары, "Номенклатура", "Гайка").Количество', {"setup": "Товары = Новый ТаблицаЗначений; Товары.Колонки.Добавить(\"Номенклатура\"); Товары.Колонки.Добавить(\"Количество\"); С = Товары.Добавить(); С.Номенклатура = \"Болт\"; С.Количество = 2; С = Товары.Добавить(); С.Номенклатура = \"Гайка\"; С.Количество = 5"}),  # noqa: E501
    ],
    "proc-tp-delete-empty": [
        ("Товары.Количество()", {"setup": "Товары = Новый ТаблицаЗначений; Товары.Колонки.Добавить(\"Количество\"); С = Товары.Добавить(); С.Количество = 0; С = Товары.Добавить(); С.Количество = 5; УдалитьПустыеСтрокиТЧ(Товары, \"Количество\")"}),  # noqa: E501
    ],
    "table-create-catalog": [
        ("КаталогТоваров().Количество()", {}),
        ("КаталогТоваров()[1].Наименование", {}),
    ],
    "table-group-sum": [
        ('ИтогиПоКатегориям(Т).Количество()', {"setup": "Т = Новый ТаблицаЗначений; Т.Колонки.Добавить(\"Категория\"); Т.Колонки.Добавить(\"Сумма\"); С = Т.Добавить(); С.Категория = \"А\"; С.Сумма = 10; С = Т.Добавить(); С.Категория = \"Б\"; С.Сумма = 20"}),  # noqa: E501
        ('ИтогиПоКатегориям(Т)[0].Сумма', {"setup": "Т = Новый ТаблицаЗначений; Т.Колонки.Добавить(\"Категория\"); Т.Колонки.Добавить(\"Сумма\"); С = Т.Добавить(); С.Категория = \"А\"; С.Сумма = 10; С = Т.Добавить(); С.Категория = \"Б\"; С.Сумма = 20"}),  # noqa: E501
    ],
    "proc-fill-structure": [
        ('С.Свойство("Дата")', {"setup": "С = Новый Структура; ЗаполнитьДатуВСтруктуре(С)"}),
    ],
    "struct-where-fields": [
        ('ВыбратьПоля(С, И).Свойство("А")', {"setup": 'С = Новый Структура("А, Б", 1, 2); И = Новый Массив; И.Добавить("А")'}),  # noqa: E501
        ('ВыбратьПоля(С, И).Свойство("Б")', {"setup": 'С = Новый Структура("А, Б", 1, 2); И = Новый Массив; И.Добавить("А")'}),  # noqa: E501
    ],
    "func-safe-map-get": [
        ('ЗначениеСоответствия(М, "а", 9)', {"setup": 'М = Новый Соответствие; М.Вставить("а", 1)'}),  # noqa: E501
        ('ЗначениеСоответствия(М, "б", 0)', {"setup": 'М = Новый Соответствие; М.Вставить("а", 1)'}),  # noqa: E501
    ],
    "proc-tp-clear-and-fill": [
        ("Товары.Количество()", {"setup": 'Товары = Новый ТаблицаЗначений; Товары.Колонки.Добавить("Номенклатура"); Товары.Колонки.Добавить("Количество"); Д = Новый Массив; Э = Новый Структура("Номенклатура, Количество", "Болт", 2); Д.Добавить(Э); ЗаполнитьТовары(Товары, Д)'}),  # noqa: E501
        ('Товары[0].Количество', {"setup": 'Товары = Новый ТаблицаЗначений; Товары.Колонки.Добавить("Номенклатура"); Товары.Колонки.Добавить("Количество"); Д = Новый Массив; Э = Новый Структура("Номенклатура, Количество", "Болт", 2); Д.Добавить(Э); ЗаполнитьТовары(Товары, Д)'}),  # noqa: E501
    ],
}

# Tasks whose checks are NOT generatable (stay L0+L2, честно фиксируется в отчёте):
# - query x5: references строят Новый Запрос (объект), а ванильный OneScript его не имеет;
#   переносить эталоны на текст запроса — отдельное решение (меняет условие задач)
# - table-create-catalog / table-group-sum: эталоны передают строки ("Число") как тип колонки,
#   OneScript требует Тип("Число") — L0-пролёт, пойманный живым оракулом
# - proc-fill-structure / struct-merge / struct-to-map / struct-where-fields /
#   func-parse-date-dot / func-safe-map-get / proc-tp-clear-and-fill: ссылки-референсы
#   используют паттерны, которые ванильный OneScript не исполняет (итерация Структуры,
#   безаргументный Новый Структура и т.п.) — кандидаты на доводку эталонов
# - skd x4: объекты компоновки отсутствуют в OneScript
# - func-nstr-parse: живой захват вернул пустую строку — слабый оракул, довести отдельно
# - proc-http-get / func-http-retry-get: требуют живое сетевое соединение


def generate(runner: OneScriptRunner, tasks: list):
    """Run references, capture outputs -> ({task_id: [ExecCheck]}, problems)."""

    by_id = {t.id: t for t in tasks}
    generated: dict[str, list[ExecCheck]] = {}
    problems: list[str] = []
    for task_id, entries in INVENTORY.items():
        task = by_id.get(task_id)
        if task is None:
            problems.append(f"{task_id}: нет такой задачи в YAML")
            continue
        probes = [ExecCheck(call=call, **kwargs) for call, kwargs in entries]
        outcome = runner.run_checks(task.reference, probes)
        if not outcome.ran:
            problems.append(f"{task_id}: движок не запустился ({outcome.error})")
            continue
        checks: list[ExecCheck] = []
        for probe, result in zip(probes, outcome.results):
            if result.error:
                problems.append(f"{task_id}: {probe.call} -> ошибка: {result.error[:90]}")
                continue
            checks.append(
                ExecCheck(
                    call=probe.call,
                    expect=result.actual,
                    setup=probe.setup,
                    case_fold=probe.case_fold,
                    proc=probe.proc,
                )
            )
        if checks:
            generated[task_id] = checks
    return generated, problems


def roundtrip(runner: OneScriptRunner, tasks: list, generated: dict) -> list[str]:
    """A4-lite: every reference must pass its own freshly generated checks."""

    by_id = {t.id: t for t in tasks}
    failures = []
    for task_id, checks in generated.items():
        outcome = runner.run_checks(by_id[task_id].reference, list(checks))
        if not outcome.passed:
            bad = [r.call for r in outcome.results if not r.passed]
            failures.append(f"{task_id}: провалили собственные чеки: {bad}")
    return failures


def render_checks(checks: list) -> list[str]:
    """YAML lines for one checks block (4-space field indent)."""

    lines = ["    checks:"]
    for check in checks:
        lines.append(f"      - call: {json.dumps(check.call, ensure_ascii=False)}")
        if check.setup:
            lines.append(f"        setup: {json.dumps(check.setup, ensure_ascii=False)}")
        lines.append(f"        expect: {json.dumps(check.expect, ensure_ascii=False)}")
        if check.case_fold:
            lines.append("        case_fold: true")
        if check.proc:
            lines.append("        proc: true")
    return lines


def insert_into_yaml(generated: dict) -> str:
    """Text-level insertion: the hand-written YAML stays untouched otherwise."""

    text = TASKS.read_text(encoding="utf-8")
    lines = text.split("\n")
    out: list[str] = []
    task_starts = {
        i: line.split("id:", 1)[1].strip()
        for i, line in enumerate(lines)
        if line.startswith("  - id:")
    }
    inserted = 0
    i = 0
    while i < len(lines):
        if i in task_starts and task_starts[i] in generated:
            out.append(lines[i])
            end = len(lines)
            for j in range(i + 1, len(lines)):
                if j in task_starts:
                    end = j
                    break
            insert_at = end
            while insert_at > i and not lines[insert_at - 1].strip():
                insert_at -= 1
            out.extend(lines[i + 1 : insert_at])
            out.extend(render_checks(generated[task_starts[i]]))
            inserted += 1
            out.extend(lines[insert_at:end])
            i = end
            continue
        out.append(lines[i])
        i += 1
    print(f"inserted checks for {inserted} task(s)")
    return "\n".join(out)


def main() -> int:
    dry = "--dry" in sys.argv
    runner = OneScriptRunner.discover()
    if runner is None:
        print("error: OneScript engine not found (set OSCRIPT_PATH)")
        return 2
    tasks = load_tasks(TASKS)
    # idempotency: never touch tasks that already carry checks
    have = {t.id for t in tasks if t.checks}
    for done in list(INVENTORY):
        if done in have:
            INVENTORY.pop(done)
    generated, problems = generate(runner, tasks)
    failures = roundtrip(runner, tasks, generated)
    for task_id in [f.split(":")[0] for f in failures]:
        generated.pop(task_id, None)

    print(f"\n=== CAPTURED ({len(generated)} tasks) ===")
    for task_id, checks in generated.items():
        for c in checks:
            shown = c.expect if len(c.expect) <= 60 else c.expect[:57] + "..."
            shown = shown.replace("\n", "\\n")
            print(f"{task_id:<34} {c.call[:44]:<46} -> {shown}")
    if problems:
        print(f"\n=== PROBLEMS ({len(problems)}) ===")
        for p in problems:
            print("  " + p)
    if failures:
        print(f"\n=== ROUNDTRIP FAILURES (excluded: {len(failures)}) ===")
        for f in failures:
            print("  " + f)

    if not dry:
        new_text = insert_into_yaml(generated)
        TASKS.write_text(new_text, encoding="utf-8")
        reloaded = load_tasks(TASKS)
        n = sum(1 for t in reloaded if t.checks)
        print(f"\nreloaded: {n}/{len(reloaded)} tasks now carry checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
