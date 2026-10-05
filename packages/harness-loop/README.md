# harness-loop

Agent loop для 1С/BSL: **генерация → верификация → (ревью) → исправление**. Соединяет существующие порты в ретрей-цикл с backpressure:

```
задача ─► LLM (russian-llm-pack, LLMPort) ─► извлечение BSL-кода
                ▲                                      │
                └── fix-промпт: код + диагностики ── bsl-verify (L0/L1)
                          ▲                            │
                          └── замечания ревьюера ── Judge (L2, опционально)
```

Модель пишет модуль → `bsl-language-server` проверяет его за секунды (ParseError = L0, ~250 диагностик = L1) → если есть ошибки уровня Error, модель получает диагностики обратно и чинит код → цикл, пока не пройдёт или не кончится бюджет итераций. Если подключён judge — после прохождения верификатора модуль получает второе мнение другой модели (L2), вето ревьюера отправляет замечания обратно в цикл. Дорогой LLM зовётся только чтобы чинить то, что нашли дешёвые детерминированные проверки.

Часть экосистемы **ai-harness-os**. Зависимости: `russian-llm-pack` + `bsl-verify` + `pyyaml` (eval-задачи). Код цикла — тонкий оркестратор без фреймворков.

## Зачем

- **Дешёвый верификатор как гейт.** Каждая итерация цикла стоит копейки (java-анализ ~50ms на файл + токены одного запроса), зато отсекает синтаксический мусор до того, как он попадёт в проект.
- **Диагностики как feedback.** Модель получает не абстрактное «код плохой», а конкретные `module.bsl:4:1 ERROR ParseError: ...` — с позициями, как в редакторе.
- **Judge как второе мнение.** Верификатор ловит синтаксис и стандарты, но не «решает ли код задачу». Judge (другая модель через тот же роутер) смотрит на задачу + код + остаточные диагностики и может наложить вето — с конкретными замечаниями в следующий промпт.
- **Полная наблюдаемость.** Каждая итерация логируется: токены, латентности LLM и верификатора, счётчики диагностик, вердикт judge, финальный код. `--json` — машиночитаемо; `--langfuse` — телеметрия в Langfuse (ключи в env, без ключей — тихий no-op).
- **Mini SWE-bench-BSL.** Встроенный набор из 70 задач (15 категорий: function / loop / branching / errors / string / structure / dates / collections / query / table / numbers / **nstr / http / skd / tablepart**; 13 easy / 39 medium / 18 hard) с reference-решениями — первый публичный бенчмарк генерации BSL. Новые категории v0.4 — паттерны реальных 1С-проектов: НСтр и многострочные литералы, HTTPСоединение/HTTPЗапрос, программная компоновка данных (СКД), табличные части через коллекции строк. `harness-loop eval` гоняет набор и отдаёт отчёт (JSON/markdown) с разбивкой по категориям и сложности — видно, *где* модель слаба, а не только средний процент. Каждое эталонное решение проверено реальным bsl-language-server (интеграционный тест: 0 Error-диагностик). Фильтры `--category` / `--difficulty` позволяют сравнивать модели на подмножествах (например, только hard или только новые категории `nstr,http,skd,tablepart`).

## Установка

```bash
cd ai-harness-os
pip install -e packages/russian-llm-pack   # порядок важен: сначала зависимости
pip install -e packages/bsl-verify
pip install -e "packages/harness-loop[dev]"
```

Нужно окружение (проверяется `harness-loop doctor`):

1. **Ключ LLM-провайдера** — например `export DEEPSEEK_API_KEY=sk-...` (роутинг и fallback — см. russian-llm-pack).
2. **Java 17+** и **bsl-language-server.jar** (см. bsl-verify: `BSL_LS_JAR` или `~/.bsl-language-server/`).

## Использование

### CLI

```bash
harness-loop doctor                          # проверить оба слоя: ключи + java/jar

# простая задача
harness-loop run "Напиши функцию СуммаДвухЧисел(А, Б), возвращающую сумму"

# с judge — второе мнение другой модели о финальном коде
harness-loop run "Напиши функцию проверки ИНН" \
    --judge --judge-chain judge \
    --langfuse

# с контекстом проекта, бюджетом и сохранением результата
harness-loop run "Процедура печати ценника" \
    --context-file AGENTS.md \
    --max-iterations 4 \
    --save module.bsl

# бенчмарк: mini SWE-bench-BSL (70 задач, отчёт JSON + markdown с разбивками)
harness-loop eval --save-report report.json --markdown report.md
harness-loop eval --limit 3 --judge --verbose  # подмножество, с judge
harness-loop eval --judge --no-judge-reference  # A/B: судья БЕЗ эталонов (как в v0.4)
harness-loop eval --difficulty hard            # только 18 hard-задач
harness-loop eval --category table,query       # только выбранные категории
harness-loop eval --category nstr,http,skd,tablepart  # только реальные 1С-паттерны v0.4

# машиночитаемый результат + прогресс итераций
harness-loop run "..." --json --verbose

# мимо роутинга — конкретная модель
harness-loop run "..." --model deepseek/deepseek-chat
```

Коды выхода: `0` — цикл прошёл верификацию (и judge, если был), `1` — бюджет исчерпан / вето judge / код не прошёл, `2` — ошибка окружения (нет ключей / java / jar) или использования. `eval` возвращает `0` при любом pass rate (это данные, не ошибка) и `2` только при ошибках окружения.

Вывод:

```
BSL loop: PASSED after 2 iteration(s) [tokens: 1450 in + 620 out; llm 3.1s, verify 9.4s]
  iter 1 [deepseek-chat]: verify FAILED (1 error, 1 warning, 0 info)
  iter 2 [deepseek-chat]: verify PASSED (0 error, 0 warning, 1 info)

```bsl
Функция СуммаДвухЧисел(А, Б)
    Возврат А + Б;
КонецФункции
```
```

### API

```python
from russian_llm_pack import Router, RouterConfig
from bsl_verify import BslVerifier, VerifyPolicy
from harness_loop import BslAgentLoop, RouterPort, LoopConfig, Judge

router = Router.from_config(RouterConfig.builtin())
llm = RouterPort(router, task="coding")                 # порт с приколотенным чейном
verifier = BslVerifier(policy=VerifyPolicy(max_errors=0))
judge = Judge(RouterPort(router, task="judge"))          # второе мнение (опционально)

loop = BslAgentLoop(llm=llm, verifier=verifier,
                    config=LoopConfig(max_iterations=3),
                    judge=judge)

result = loop.run("Напиши функцию СуммаДвухЧисел(А, Б)")

result.passed                    # True если верификатор (и judge) приняли код
result.code                      # финальный BSL-модуль
result.judge                     # вердикт ревьюера (approved/score/issues)
result.judge_error               # не None, если judge LLM был недоступен
result.iterations                # IterationLog: токены, латентности, диагностики
result.summary_lines()           # человекочитаемый отчёт
result.to_dict()                 # JSON-safe
```

Семантика judge (важно):

- **Вето явное.** Парсер терпим к формату ответа, но FAIL засчитывается только при явном VERDICT: FAIL (или явном «отклонено» в тексте). Ответ без распознанного вердикта = PASS c пометкой `parsed=False` — верификатор остаётся авторитетом.
- **Недоступность judge ≠ провал.** Если judge-LLM упал (нет ключей/чейн исчерпан), код, уже прошедший верификатор, НЕ отбрасывается: `passed=True` + громкий `judge_error` в отчёте.
- **Свои чейны.** Judge получает собственный `RouterPort` — можно оценить код моделью другого провайдера (`--judge-chain judge` в rlp-конфиге).
- **Judge против эталонов (v0.5).** В eval-прогонах судья получает reference-решение задачи и сравнивает **семантику** (формулы, границы условий, off-by-one, граничные случаи, возвращаемый результат) — отдельный системный промпт `JUDGE_REFERENCE_SYSTEM_PROMPT`. Эталон видит ТОЛЬКО судья: генератор его не получает никогда (иначе модель скопирует эталон и бенчмарк обесценится — инвариант протестирован). A/B-сравнение со старым режимом: `eval --judge --no-judge-reference`.
- **Последний вердикт выживает (v0.5).** `LoopResult.judge` — последний вердикт судьи даже при вето, переживший бюджет: отчёты eval сохраняют score/issues отклонённых задач (раньше ветро терялось и judge оставался None).

### Телеметрия Langfuse

```python
from harness_loop import LangfuseTelemetry

telemetry = LangfuseTelemetry(trace_id="run-42")          # env: LANGFUSE_*_KEY
router = Router.from_config(rc, on_event=telemetry.on_router_event)
result = loop.run(task, on_iteration=telemetry.on_iteration)
telemetry.flush()                                        # один батч POST
```

Ноль зависимостей (stdlib urllib), ключи только по именам env-переменных, отсутствие ключей — тихий no-op, сетевые ошибки проглатываются. В пейлоады не попадают ни код, ни промпты — только метрики и счётчики.

## Как это работает

```
run(task, context)
  ├── итерация 1: task_prompt (системный + задача + контекст)
  │       └── LLMPort.complete → extract_bsl_code (```bsl фенс / эвристика)
  │               └── BslVerifier.verify_module_text → VerifyResult
  │                       ├── passed (+ judge approves / judge нет)
  │                       │        → LoopResult(passed=True)           ← стоп
  │                       ├── passed, но judge отклонил → замечания в след. промпт
  │                       └── FAILED → format_diagnostics (Errors первыми, кап строк)
  ├── итерация 2..N: fix_prompt ИЛИ judge_fix_prompt → тот же путь
  └── стоп-условия: passed | бюджет итераций | judge_rejected | RLLError | BslVerifyError
```

- **Извлечение кода** терпимо к ответам модели: фенс с тегом `bsl`/без тега/с чужим тегом, несколько фенсов (берём последний — финальную версию), чистый BSL без фенса, проза без кода → итерация «пустая», модель получает замечание о формате.
- **Feedback-промпт** ограничен (`max_feedback_lines`, дефолт 25), диагностики сортированы по severity: сначала Error — то, что обязан починить.
- **Ошибки не бросаются наружу**: `RLLError` (нет ключей / цепь исчерпана) и `BslVerifyError` (нет java/jar) превращаются в `failure_reason`, CLI мапит их в exit 2.

## Методология: что измеряет бенчмарк

Бенчмарк измеряет три независимых уровня — и каждый отчёт показывает все три, а не одну цифру:

| Уровень | Что проверяет | Инструмент | Когда измеряется |
|---|---|---|---|
| **L0** | синтаксис и статические диагностики (политика верификатора) | bsl-language-server | всегда |
| **L1** | исполнение: модуль запускается в OneScript и печатает ожидаемые значения | OneScript v2.2 (ExecutorPort) | только для задач с чеками |
| **L2** | семантика: judge сравнивает решение с эталонным | LLM-цепь (reference-aware) | если подключён `--judge` |

Поля отчёта: `resolved_l0` (синоним исторического `resolved`), `resolved_l1`, `l1_measured` (у скольких задач оракул реально запускался) и `l1_coverage` — доля задач с исполняемыми чеками. Движок не установлен → L1 честно «не измерялся», а не «0 из N».

**Чеки.** 59 из 70 задач (84%) несут секцию `checks:` — входные вызовы и ожидаемые значения. Ожидания не придуманы руками: они захвачены прогоном эталонных решений на реальном движке (`scripts/gen_checks.py`, правило «реальность > предположений») и зафиксированы round-trip гейтом: эталон обязан проходить собственные чеки (интеграционный тест, маркер `integration`). Сетевые задачи проверяются против локального стаб-сервера исполнителя (127.0.0.1, без внешних хостов): маршрут `/flaky` обрывает первые два соединения, так что логика повторов испытывается по-настоящему.

**Что не исполняется и почему (честные ограничения, 11 задач):**

- `query` (5) — эталоны строят `Новый Запрос`; в ванильном OneScript объекта «Запрос» нет;
- `skd` (4) — объекты компоновки данных существуют только внутри платформы 1С;
- `struct-merge`, `struct-to-map` (2) — семантика итерации `Структуры` в 1С (переменной цикла присваивается строка-ключ) и в OneScript (объект `КлючИЗначение`) расходится: каноничное 1С-решение падает в движке на `Базовая[Ключ]` — L1-чек был бы несправедлив к корректным решениям;
- сетевые задачи (2) ИСППОЛНЯЮТСЯ с 0.8.0: `http_stub`-чеки против локального стаба; `struct-where-fields` (двухаргументный `Свойство()` поддержан движком — прежняя запись о несовместимости опровергнута живой пробой), `func-parse-date-dot` и `func-nstr-parse` тоже под оракулом.

Публикация любых цифр наружу — только со ссылкой на этот раздел: одна цифра без уровня и покрытия ничего не значит.

## Контекст проекта (фаза C дорожной карты 2.1)

Сокет `context` больше не пуст: `harness-loop run "задача" --context-project <каталог>`
запускает индексацию проекта и вливает результат в промпты генерации и фиксов.
Явный `--context`/`--context-file` всегда приоритетнее индексатора.

**Порт `ContextProviderPort.collect(project_path, task, max_tokens)`** с двумя адаптерами:

| Адаптер | Что делает | Включение |
|---|---|---|
| `BuiltInIndexer` | чистый stdlib: rglob `*.bsl`/`*.os`, сигнатуры процедур/функций (до 40 на модуль), ссылки на метаданные (`Справочник.*`…), ранжирование по релевантности к тексту задачи, жёсткий токен-бюджет (дефолт 8000, `--context-budget`) | по умолчанию |
| `McpIndexerBackend` | subprocess-клиент к внешнему code-index MCP-серверу: spawn → initialize → tools/list → tools/call; любая неудача тихо откатывает на `BuiltInIndexer` с предупреждением в note | `HARNESS_CONTEXT=mcp` + `CODE_INDEX_MCP_PATH` |

Выбор адаптера — одна переменная окружения: `HARNESS_CONTEXT=builtin|mcp|none`
(дефолт `builtin`). Наблюдаемость: источник и размер контекста попадают в каждый
`IterationLog` (`context_source` / `context_tokens`) — видно в отчётах и в MCP-тулинге
`run_loop` (аргумент `project_path`).

Ограничения v0.1 (честно): встроенный индексер не строит реверс-индекс вызовов —
это точка роста Phase 2; MCP-бэкенд знает каталог внешнего сервера только по именам
тулингов (передаёт `{query, path}`), матчинг инструмента — по предпочтительным
подстрокам (настраивается `CODE_INDEX_MCP_TOOLS`).

A/B-демонстрация эффекта: `python scripts/ab_context.py` — синтетическое дерево из
категорий бенчмарка, две руки (bare/context), сравнение L0/L1/L2. Требует настроенный
LLM; без ключей печатает дерево и образец контекста.

## Разработка

```bash
pip install -e "packages/harness-loop[dev]"
pytest -q                                            # юнит: фейки, без сети/java/токенов
BSL_LS_JAR=... pytest -m integration -v             # fake LLM + реальный bsl LS
OSCRIPT_PATH=... pytest -m integration -v           # L1-оракул на реальном OneScript
DEEPSEEK_API_KEY=... BSL_LS_JAR=... pytest -m live -v  # полный живой цикл
```

## Roadmap

- **v0.1** — цикл генерация→верификация→фикс ✅
- **v0.2** — judge-цепь (L2), телеметрия Langfuse, mini SWE-bench-BSL (10 задач) ✅
- **v0.3** — бенчмарк 10 → 30 задач (dates/collections/query, hard-уровень), интеграционный гейт эталонов на реальном LS ✅
- **v0.4** — бенчмарк 30 → 54 задачи (table/numbers, 13 hard против насыщения), разбивки по категориям/сложности в отчётах, фильтры `--category`/`--difficulty` ✅
- **v0.5** — reference-aware judge: судья сравнивает семантику с эталоном (генератор его не видит), L2-агрегаты в отчётах (judge_mode, approved/vetoed, avg score), `--no-judge-reference` для A/B ✅
- **v0.6** — рост набора под реальные 1С-проекты: 54 → 70 задач (nstr/http/skd/tablepart, 15 категорий, 18 hard) ✅; движок DeepAgents/LangGraph для задач с планированием — этот цикл остаётся эталоном поведения и reference-контрактом портов
- **v0.7** — исполнительный оракул L1: `ExecutorPort` + `OneScriptRunner` (изоляция: temp-каталог, чистое окружение, таймаут), формат чеков в задачах, метрики L0/L1/L2 + `l1_coverage` в отчётах, 54 задачи с живыми чеками (77%), интеграционный гейт эталонов ✅
- **v0.8** — `http_stub`-чеки: сетевые задачи под живым оракулом (локальный стаб-сервер, ретраи через обрыв соединения), 59/70 задач с живыми чеками (84%); сплиттер setup уважает кавычки; уточнение границ неисполняемого — 11 задач с причинами ✅
- **v0.9** — контекстный слой: `ContextProviderPort` + `BuiltInIndexer` (сигнатуры+метаданные+релевантность+бюджет) + `McpIndexerBackend` (subprocess, тихий фоллбек), `--context-project`/`--context-budget` в CLI, `context_source`/`context_tokens` в IterationLog, A/B-скрипт ✅

## Лицензия

MIT. См. [корневой LICENSE](../../LICENSE).
