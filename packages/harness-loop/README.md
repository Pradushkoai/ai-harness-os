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
- **Mini SWE-bench-BSL.** Встроенный набор из 10 задач с reference-решениями — первый публичный бенчмарк генерации BSL. `harness-loop eval` гоняет его и отдаёт отчёт (JSON/markdown).

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

# бенчмарк: mini SWE-bench-BSL (10 задач, отчёт JSON + markdown)
harness-loop eval --save-report report.json --markdown report.md
harness-loop eval --limit 3 --judge --verbose  # подмножество, с judge

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

## Разработка

```bash
pip install -e "packages/harness-loop[dev]"
pytest -q                                            # юнит: фейки, без сети/java/токенов
BSL_LS_JAR=... pytest -m integration -v             # fake LLM + реальный bsl LS
DEEPSEEK_API_KEY=... BSL_LS_JAR=... pytest -m live -v  # полный живой цикл
```

## Roadmap

- **v0.1** — цикл генерация→верификация→фикс ✅
- **v0.2** — judge-цепь (L2), телеметрия Langfuse, mini SWE-bench-BSL (10 задач) ✅
- **v0.3** — рост набора бенчмарка (50+ задач, категории под реальные 1С-проекты), judge-промпты против reference-решений
- **v0.4** — движок DeepAgents/LangGraph для задач с планированием; этот цикл остаётся эталоном поведения и reference-контрактом портов

## Лицензия

MIT. См. [корневой LICENSE](../../LICENSE).
