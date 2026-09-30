# AGENTS.md — ai-harness-os

Правила проекта для AI-агентов (Cline / opencode / Claude Code / GLM) и людей.

## Проект

Монорепозиторий открытых пакетов ИИ-инфраструктуры для 1С-разработки: `russian-llm-pack` (LLM-слой с роутингом), `bsl-verify` (статическая проверка BSL), `harness-loop` (agent loop поверх обоих), `agents-md` (генератор AGENTS.md, самостоятельно). Пакеты ниже по стеку должны оставаться самодостаточными — их знает только `harness-loop`; `agents-md` ни от кого не зависит (чистый stdlib).

## Жёсткие правила

1. **Секреты — только env.** Ни ключей, ни токенов в коде, тестах, конфигах, логах, коммит-месседжах. Провайдеры ссылаются на *имена* env-переменных (`api_key_env`). `.env` в `.gitignore` навсегда.
2. **Пакеты независимы снизу.** `bsl-verify` не импортирует `russian_llm_pack` и наоборот — они встречаются только в `harness-loop` (это единственный пакет, которому разрешено импортировать оба). Общие абстракции выделяем только при третьем повторении.
3. **Не переинженерь.** Фича добавляется когда харнесс её реально потребил. «Понадобится потом» — не причина.
4. **Тесты без мира.** Юнит-тесты не ходят в сеть, не зовут java, не тратят токены. Всё внешнее — инъекция (fake subprocess, fake LLM client, fake provider). Живые проверки — маркеры `live`/`integration`, запускаются явно.
5. **Реальность > предположения.** Формат внешнего инструмента фиксируется живым прогоном и кладётся в тест-фикстуру (пример: `bsl-verify/tests/fixtures/sample_report.json` — реальный вывод bsl-language-server v1.0.7). Парсеры внешних форматов пишутся толерантными.
6. **Формат** — ruff-совместимый (line length 100), docstrings на английском, README и пользовательские сообщения — на русском.
7. **Структура пакета:** `src/<name>/` + `tests/` + свой `pyproject.toml` + свой CHANGELOG. Новый пакет = новая директория в `packages/` + строка в матрице CI + строка в корневом README.

## Структура

```
packages/
├── russian-llm-pack/          # LLM-слой
│   └── src/russian_llm_pack/
│       ├── ports/             # LLMPort — единственный стабильный интерфейс
│       ├── providers/         # generic OpenAI-compat движок + пресеты
│       ├── core/              # config (YAML+env), router (fallback, события)
│       └── cli.py             # rlp check / rlp chat
├── bsl-verify/                # BSL-верификация (backpressure L0/L1)
│   └── src/bsl_verify/
│       ├── types.py           # Diagnostic/Severity/VerifyResult
│       ├── parser.py          # парсер реального JSON-отчёта bsl LS
│       ├── policy.py          # VerifyPolicy — политика как код
│       ├── runner.py          # java/jar discovery + subprocess
│       ├── verifier.py        # BslVerifier: dir / files / module_text
│       └── cli.py             # bsl-check / bsl-doctor
├── harness-loop/              # agent loop: генерация→верификация→фикс
    └── src/harness_loop/
        ├── types.py           # LoopConfig / IterationLog / LoopResult
        ├── extract.py         # извлечение BSL из ответа LLM (фенсы/эвристика)
        ├── prompt.py          # системный / task / fix промпты
        ├── loop.py            # BslAgentLoop + RouterPort (Router→LLMPort)
        └── cli.py             # harness-loop run / doctor
└── agents-md/                 # генератор AGENTS.md (зависимостей нет)
    └── src/agents_md/
        ├── types.py           # ProjectInfo / StructureEntry / KIND_*
        ├── detect.py          # анализ: 1C-edt / 1C-xml / python / js-ts / generic
        ├── structure.py       # ограниченный сканер каталогов + дерево
        ├── generate.py        # RU-шаблоны по типу проекта
        ├── validate.py        # UTF-8 / 32 KiB / обязательные разделы
        └── cli.py             # agents-md init / validate
```

## Ключевые контракты

- **russian-llm-pack:** харнесс знает только `LLMPort`. Retry-политика в Router, не в SDK (`max_retries=0`). Классификация ошибок: auth → скип провайдера; transient → retry; request → следующая модель. Провайдер без ключа не ломает систему.
- **bsl-verify:** путь данных `staging dir → analyze -r json → parser → policy → VerifyResult`. Exit-коды CLI: 0 прошёл / 1 нарушения / 2 окружение. Позиции LSP 0-based внутри, 1-based в человекочитаемом выводе. Отфильтрованные политикой диагностики не существуют нигде.
- **harness-loop:** цикл `LLM → extract → verify → fix-промпт с диагностиками → ...` со стоп-условиями passed / бюджет / llm_error / verifier_error. Доменные ошибки НЕ бросаются наружу — превращаются в `failure_reason`. `RouterPort` — единственная точка входа роутинга в цикл. Feedback-промпты капятся (`max_feedback_lines`), диагностики сортированы: Error первыми. CLI: exit 0/1/2 в конвенции репо, `--version` обязан жить на корневом И сабпарсерах (урок bsl-check).
- **agents-md:** анализ только по файловой системе — никогда не исполняет код проекта и не читает `.env`. Сгенерированный файл — черновик: человек проверяет и коммитит. Лимит 32 KiB — каскадный лимит стандарта, не наш каприз. Валидатор принимает RU-алиасы обязательных разделов. Никаких зависимостей и шаблонизаторов — только stdlib.

## Установка

```bash
pip install -e "packages/russian-llm-pack[dev]"   # порядок важен: сначала нижние слои
pip install -e "packages/bsl-verify[dev]"
pip install -e "packages/harness-loop[dev]"
pip install -e "packages/agents-md[dev]"
```

## Тестирование

```bash
cd packages/russian-llm-pack && pytest -q     # юнит
cd packages/bsl-verify && pytest -q           # юнит
cd packages/harness-loop && pytest -q         # юнит (фейки, без мира)
cd packages/agents-md && pytest -q            # юнит (tmp-проекты)
# опционально, с окружением:
BSL_LS_JAR=... pytest -m integration -v       # живой bsl LS (bsl-verify, harness-loop)
DEEPSEEK_API_KEY=... pytest -m live -v        # живой LLM (russian-llm-pack, harness-loop)
```

## Куда расти (по порядку)

1. judge-цепь в `harness-loop` (второе мнение другой модели о финальном коде) + телеметрия итераций в Langfuse через события роутера
2. Skill Registry client
3. Движок DeepAgents/LangGraph для задач с планированием и подзадачами — потребляет те же порты (`LLMPort`, `BslVerifier`), текущий цикл остаётся эталоном поведения
