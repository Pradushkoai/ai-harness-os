# AGENTS.md — ai-harness-os

Правила проекта для AI-агентов (Cline / opencode / Claude Code / GLM) и людей.

## Проект

Монорепозиторий открытых пакетов ИИ-инфраструктуры для 1С-разработки. Сейчас: `russian-llm-pack` (LLM-слой с роутингом) и `bsl-verify` (статическая проверка BSL). Харнесс, который их соединит, будет строиться отдельно — эти пакеты должны оставаться самодостаточными.

## Жёсткие правила

1. **Секреты — только env.** Ни ключей, ни токенов в коде, тестах, конфигах, логах, коммит-месседжах. Провайдеры ссылаются на *имена* env-переменных (`api_key_env`). `.env` в `.gitignore` навсегда.
2. **Пакеты независимы.** `bsl-verify` не импортирует `russian_llm_pack` и наоборот — они встречаются только в будущем харнессе. Общие абстракции выделяем только при третьем повторении.
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
└── bsl-verify/                # BSL-верификация (backpressure L0/L1)
    └── src/bsl_verify/
        ├── types.py           # Diagnostic/Severity/VerifyResult
        ├── parser.py          # парсер реального JSON-отчёта bsl LS
        ├── policy.py          # VerifyPolicy — политика как код
        ├── runner.py          # java/jar discovery + subprocess
        ├── verifier.py        # BslVerifier: dir / files / module_text
        └── cli.py             # bsl-check / bsl-doctor
```

## Ключевые контракты

- **russian-llm-pack:** харнесс знает только `LLMPort`. Retry-политика в Router, не в SDK (`max_retries=0`). Классификация ошибок: auth → скип провайдера; transient → retry; request → следующая модель. Провайдер без ключа не ломает систему.
- **bsl-verify:** путь данных `staging dir → analyze -r json → parser → policy → VerifyResult`. Exit-коды CLI: 0 прошёл / 1 нарушения / 2 окружение. Позиции LSP 0-based внутри, 1-based в человекочитаемом выводе. Отфильтрованные политикой диагностики не существуют нигде.

## Команды

```bash
pip install -e "packages/russian-llm-pack[dev]"
pip install -e "packages/bsl-verify[dev]"
cd packages/russian-llm-pack && pytest -q     # юнит
cd packages/bsl-verify && pytest -q           # юнит
# опционально, с окружением:
BSL_LS_JAR=... pytest -m integration -v       # живой bsl LS (packages/bsl-verify)
DEEPSEEK_API_KEY=... pytest -m live -v        # живой LLM (packages/russian-llm-pack)
```

## Куда расти (по порядку)

1. AGENTS.md Generator (генерация контекста репо для агентов по 1С-проектам)
2. Skill Registry client
3. Интеграция: agent loop на DeepAgents + LangGraph, где `LLMPort` даёт модели, а `BslVerifier` — верификацию каждого изменения (backpressure L0/L1)
