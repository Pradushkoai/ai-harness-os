# harness-loop

Agent loop MVP для 1С/BSL: **генерация → верификация → исправление**. Первый настоящий «продукт» харнесса — соединяет два существующих порта в ретрей-цикл с backpressure:

```
задача ─► LLM (russian-llm-pack, LLMPort) ─► извлечение BSL-кода
                ▲                                      │
                └── fix-промпт: код + диагностики ── bsl-verify (L0/L1)
```

Модель пишет модуль → `bsl-language-server` проверяет его за секунды (ParseError = L0, ~250 диагностик = L1) → если есть ошибки уровня Error, модель получает диагностики обратно и чинит код → цикл, пока не пройдёт или не кончится бюджет итераций. Дорогой LLM зовётся только чтобы чинить то, что нашли дешёвые детерминированные проверки.

Часть экосистемы **ai-harness-os**. Зависимости: `russian-llm-pack` + `bsl-verify` (из этого же монорепо). Код цикла — тонкий оркестратор без фреймворков.

## Зачем

- **Дешёвый верификатор как гейт.** Каждая итерация цикла стоит копейки (java-анализ ~50ms на файл + токены одного запроса), зато отсекает синтаксический мусор до того, как он попадёт в проект.
- **Диагностики как feedback.** Модель получает не абстрактное «код плохой», а конкретные `module.bsl:4:1 ERROR ParseError: ...` — с позициями, как в редакторе.
- **Полная наблюдаемость.** Каждая итерация логируется: токены, латентности LLM и верификатора, счётчики диагностик, финальный код. `--json` — машиночитаемо.

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

# с контекстом проекта, бюджетом и сохранением результата
harness-loop run "Процедура печати ценника" \
    --context-file AGENTS.md \
    --max-iterations 4 \
    --save module.bsl

# машиночитаемый результат + прогресс итераций
harness-loop run "..." --json --verbose

# мимо роутинга — конкретная модель
harness-loop run "..." --model deepseek/deepseek-chat
```

Коды выхода: `0` — цикл прошёл верификацию, `1` — бюджет итераций исчерпан, код так и не прошёл, `2` — ошибка окружения (нет ключей / java / jar) или использования.

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
from harness_loop import BslAgentLoop, RouterPort, LoopConfig

router = Router.from_config(RouterConfig.builtin())
llm = RouterPort(router, task="coding")                 # порт с приколотым чейном
verifier = BslVerifier(policy=VerifyPolicy(max_errors=0))

loop = BslAgentLoop(llm=llm, verifier=verifier,
                    config=LoopConfig(max_iterations=3))

result = loop.run("Напиши функцию СуммаДвухЧисел(А, Б)")

result.passed                    # True если верификатор принял финальный код
result.code                      # финальный BSL-модуль
result.iterations                # IterationLog: токены, латентности, диагностики
result.summary_lines()           # человекочитаемый отчёт
result.to_dict()                 # JSON-safe
```

## Как это работает

```
run(task, context)
  ├── итерация 1: task_prompt (системный + задача + контекст)
  │       └── LLMPort.complete → extract_bsl_code (```bsl фенс / эвристика)
  │               └── BslVerifier.verify_module_text → VerifyResult
  │                       ├── passed → LoopResult(passed=True)   ← стоп
  │                       └── FAILED → format_diagnostics (Errors первыми, кап строк)
  ├── итерация 2..N: fix_prompt (задача + прошлый код + диагностики) → тот же путь
  └── стоп-условия: passed | бюджет итераций | RLLError | BslVerifyError (окружение)
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

- **v0.1** — цикл как выше ✅
- **v0.2** — judge-цепь (второе мнение другой модели о финальном коде), телеметрия итераций в Langfuse (через события роутера)
- **v0.3** — движок DeepAgents/LangGraph для задач с планированием и подзадачами; этот цикл останется эталоном поведения и reference-контрактом портов

## Лицензия

MIT. См. [корневой LICENSE](../../LICENSE).
