# AGENTS.md — ai-harness-os

Правила проекта для AI-агентов (Cline / opencode / Claude Code / GLM) и людей.

## Проект

Монорепозиторий открытых пакетов ИИ-инфраструктуры для 1С-разработки: `russian-llm-pack` (LLM-слой с роутингом), `bsl-verify` (статическая проверка BSL), `harness-loop` (agent loop поверх обоих), `agents-md` (генератор AGENTS.md, самостоятельно), `harness-mcp` (MCP-сервер поверх harness-loop — доступ агентов к харнессу). Пакеты ниже по стеку должны оставаться самодостаточными — их знает только `harness-loop`; `agents-md` ни от кого не зависит (чистый stdlib); `harness-mcp` — верхний слой, зависит от `harness-loop`.

## Жёсткие правила

1. **Секреты — только env.** Ни ключей, ни токенов в коде, тестах, конфигах, логах, коммит-месседжах. Провайдеры ссылаются на *имена* env-переменных (`api_key_env`). `.env` в `.gitignore` навсегда.
2. **Пакеты независимы снизу.** `bsl-verify` не импортирует `russian_llm_pack` и наоборот — они встречаются только в `harness-loop` (это единственный пакет, которому разрешено импортировать оба). Общие абстракции выделяем только при третьем повторении.
3. **Не переинженерь.** Фича добавляется когда харнесс её реально потребил. «Понадобится потом» — не причина.
4. **Тесты без мира.** Юнит-тесты не ходят в сеть, не зовут java, не тратят токены. Всё внешнее — инъекция (fake subprocess, fake LLM client, fake provider). Живые проверки — маркеры `live`/`integration`, запускаются явно.
5. **Реальность > предположения.** Формат внешнего инструмента фиксируется живым прогоном и кладётся в тест-фикстуру (пример: `bsl-verify/tests/fixtures/sample_report.json` — реальный вывод bsl-language-server v1.0.7). Парсеры внешних форматов пишутся толерантными.
6. **Формат** — ruff-совместимый (line length 100; CI-гейт: `E4,E7,E9,F,E501`), docstrings на английском, README и пользовательские сообщения — на русском.
7. **Структура пакета:** `src/<name>/` + `tests/` + свой `pyproject.toml` + свой CHANGELOG. Новый пакет = новая директория в `packages/` + строка в матрице CI (ubuntu + windows) + строка в корневом README.
8. **Windows — first-class.** Платформа разработки и пилотов 1С: тесты не содержат POSIX-допущений (ожидания путей — через `os.path.normpath`/`as_uri()`, имена исполняемых файлов — с учётом `os.name == "nt"`). CI гоняет юнит-тесты и на `windows-latest`.

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
├── harness-loop/              # agent loop: генерация→верификация→(judge)→фикс
│   └── src/harness_loop/
│       ├── types.py           # LoopConfig / IterationLog / LoopResult
│       ├── extract.py         # извлечение BSL из ответа LLM (фенсы/эвристика)
│       ├── prompt.py          # системный / task / fix / judge промпты
│       ├── judge.py           # LLM-as-judge: вердикт, толерантный парсер, вето
│       ├── telemetry.py       # Langfuse: on_event/on_iteration → ingestion API
│       ├── evals.py           # mini SWE-bench-BSL: задачи, раннер, отчёты
│       ├── eval_data/         # tasks_v0.yaml — 70 задач (15 категорий), 59 с L1-чеками
│       ├── executors.py       # L1-оракул: ExecutorPort + OneScriptRunner (изоляция, таймаут)
│       ├── context.py         # ContextProviderPort + BuiltInIndexer + McpIndexerBackend (фаза C)
│       ├── loop.py            # BslAgentLoop + RouterPort (Router→LLMPort)
│       └── cli.py             # harness-loop run / eval / doctor
├── agents-md/                 # генератор AGENTS.md (зависимостей нет)
│   └── src/agents_md/
│       ├── types.py           # ProjectInfo / StructureEntry / KIND_*
│       ├── detect.py          # анализ: 1C-edt / 1C-xml / python / js-ts / generic
│       ├── structure.py       # ограниченный сканер каталогов + дерево
│       ├── generate.py        # RU-шаблоны по типу проекта
│       └── validate.py        # UTF-8 / 32 KiB / обязательные разделы
└── harness-mcp/               # MCP-выход: stdio-сервер для агентов (фаза B)
    └── src/harness_mcp/
        ├── protocol.py        # JSON-RPC 2.0 + MCP: initialize/tools/notifications
        ├── tools.py           # 5 тулингов поверх готовых портов (ping/…)
        ├── state.py           # сессионный кэш run_id с TTL (без персистентности)
        └── server.py          # stdio-цикл: строка = сообщение
        └── cli.py             # agents-md init / validate
scripts/
└── setup.sh                   # установка+тесты за один запуск (Git Bash/WSL/Linux/macOS)
```

## Ключевые контракты

- **russian-llm-pack:** харнесс знает только `LLMPort`. Retry-политика в Router, не в SDK (`max_retries=0`). Классификация ошибок: auth → скип провайдера; transient → retry; request → следующая модель. Провайдер без ключа не ломает систему. Пресет `qwen` (DashScope compatible-mode): ключ `QWEN_API_KEY` или алиас `DASHSCOPE_API_KEY` (механизм `alt_key_envs`; алиасы отключаются при override `api_key_env` в конфиге), intl-endpoint по умолчанию, mainland — через `base_url`. Judge-цепь начинается с `qwen/qwen-max` — контракт независимости судьи (первый провайдер judge-цепи ≠ первый провайдер coding-цепи) закреплён тестом. Нативные адаптеры (yandexgpt, gigachat) — на stdlib-транспорте `providers/_http.py`: OAuth/GigaChat и modelUri/YandexGPT — внутреннее дело адаптера, наружу тот же порт. IAM-токены и OAuth-токены НЕ рефрешатся глобально: IAM (~12ч) — вручную, GigaChat access (~30 мин) — сам адаптер. `yandexgpt` замыкает все builtin-цепи (санкционная устойчивость).
- **bsl-verify:** путь данных `staging dir → analyze -r json → parser → policy → VerifyResult`. Exit-коды CLI: 0 прошёл / 1 нарушения / 2 окружение. Позиции LSP 0-based внутри, 1-based в человекочитаемом выводе. Отфильтрованные политикой диагностики не существуют нигде.
- **harness-loop:** цикл `LLM → extract → verify → fix-промпт → ...` (+ judge после прохождения верификатора). Стоп-условия: passed / бюджет / judge_rejected / llm_error / verifier_error. Доменные ошибки НЕ бросаются наружу — превращаются в `failure_reason`. `RouterPort` — единственная точка входа роутинга в цикл (у judge — свой экземпляр с другим чейном). Judge: вето только явное (нет распознанного вердикта = PASS c `parsed=False`), RLLError judge не отбрасывает код, прошедший верификатор (громкий `judge_error`); `LoopResult.judge` — последний вердикт (включая вето, пережившее бюджет). Reference-aware judge (v0.5): в eval судья сравнивает семантику с эталоном (`JUDGE_REFERENCE_SYSTEM_PROMPT`), **эталон — вход ТОЛЬКО судьи**: утечка в промпты генератора обесценивает бенчмарк (инвариант протестирован на loop/run_eval/CLI). Телеметрия `--langfuse`: stdlib-клиент, ключи только по именам env, no-op без ключей, сетевые ошибки проглатываются, код/промпты не шлёт. Eval: `resolved = loop.passed` (= L0); отчёты несут три уровня — `resolved_l0/resolved_l1/l1_measured/l1_coverage` + L2-агрегаты (`judge_mode`/approved/vetoed/avg score); exit 0 при любом pass rate. L1-оракул (`executors.py`): чеки задач исполняются OneScript в temp-каталоге с вычищенным окружением (никаких API-ключей дочернему процессу) и таймаутом; движка нет → уровень «не измерялся», а не 0. Ожидания чеков генерируются ТОЛЬКО прогоном эталонов на живом движке (`scripts/gen_checks.py`, round-trip гейт) — руками ожидания не пишем (реальность > предположений). Уровни независимы: L0-pass при L1-fail (и наоборот) — легальная дивергенция, её видно в отчёте. CLI: exit 0/1/2 в конвенции репо, `--version` обязан жить на корневом И сабпарсерах (урок bsl-check).
- **agents-md:** анализ только по файловой системе — никогда не исполняет код проекта и не читает `.env`. Сгенерированный файл — черновик: человек проверяет и коммитит. Лимит 32 KiB — каскадный лимит стандарта, не наш каприз. Валидатор принимает RU-алиасы обязательных разделов. Никаких зависимостей и шаблонизаторов — только stdlib.

## Установка

Пакеты ставятся из подпапок — в корне репо НЕТ `pyproject.toml` (`pip install -e .` из корня упадёт). Всегда venv-первый флоу — или одним скриптом `bash scripts/setup.sh` (он же умеет `--clone`):

```bash
python -m venv .venv && source .venv/bin/activate   # Windows Git Bash; PowerShell: .venv\Scripts\Activate.ps1
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
OSCRIPT_PATH=... pytest -m integration -v     # живой OneScript — гейт L1-чеков эталонов
DEEPSEEK_API_KEY=... pytest -m live -v        # живой LLM (russian-llm-pack, harness-loop)
QWEN_API_KEY=... pytest -m live -v            # живой smoke Qwen (судья)
```

## Куда расти (по порядку)

1. live-прогон 70 задач с судьёй-независимостью (DeepSeek кодит, Qwen судит; L0/L1/L2-цифры — эталоны уже обязаны проходить собственные чеки, A/B через `--no-judge-reference`; срез новых категорий — `--category nstr,http,skd,tablepart`; для L1 — установить OneScript и `OSCRIPT_PATH`)
2. real-world пилот на одном BSL-проекте (10–20 задач через `harness-loop eval`)
3. Движок DeepAgents/LangGraph как optional backend на тех же портах (`LLMPort`, `BslVerifier`), текущий цикл остаётся эталоном поведения
