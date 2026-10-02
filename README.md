# ai-harness-os

Открытые пакеты ИИ-инфраструктуры для разработки под 1С (и не только). Монорепозиторий: каждый пакет — самостоятельный distributable со своим релизным циклом.

Позиционирование: **сборка харнесса для 1С-разработки из тонких, тестируемых модулей** — LLM-слой, сенсоры верификации, генераторы контекста — без переусложнения и с санкционно-устойчивым стеком.

## Пакеты

| Пакет | Что это | Статус |
|---|---|---|
| [`russian-llm-pack`](packages/russian-llm-pack/) | Единый LLM-порт: DeepSeek / Z.ai / Qwen / GigaChat (OAuth) / YandexGPT (нативный), YAML-роутинг задач, fallback-цепочки, CLI `rlp` | v0.3.0 — DeepSeek живой, Qwen ведёт judge-цепь |
| [`bsl-verify`](packages/bsl-verify/) | Статическая проверка 1С/BSL через bsl-language-server: backpressure L0/L1, политика как код, CLI `bsl-check` / `bsl-doctor` | v0.1.1 — работает вживую (v1.0.7 LS) |
| [`harness-loop`](packages/harness-loop/) | Цикл «генерация → верификация → (ревью) → исправление»: LLM пишет BSL-модуль, верификатор гейтит, judge (вторая модель) накладывает вето — в eval судья сравнивает семантику с эталоном (reference-aware, генератор его не видит); телеметрия Langfuse; mini SWE-bench-BSL (70 задач, 15 категорий incl. nstr/http/skd/tablepart — реальные 1С-паттерны, 18 hard; фильтры `--category`/`--difficulty`; L2-агрегаты в отчётах); CLI `harness-loop run/eval/doctor` | v0.6.0 — эталоны всех 70 задач проверены реальным LS |
| [`agents-md`](packages/agents-md/) | Генератор AGENTS.md для AI-агентов: анализ проекта (1C-EDT / 1C-XML / python / js-ts / generic), RU-шаблоны, валидатор стандарта; CLI `agents-md init/validate` | v0.1.0 — чистый stdlib, dogfooded на этом репо |

**Дальше по плану (дорожная карта 2.0, 12 недель):** исполнительный оракул L1 поверх OneScript — разделение метрик L0/L1/L2 и честное покрытие по категориям → пакет `harness-mcp` (stdio MCP-сервер, 5 тулингов поверх готовых портов — доступ из Claude Code / opencode / Cline) → контекстный слой (`ContextProviderPort`: встроенный индексер + опциональный code-index-mcp бэкенд) → калибровка судьи (ручная разметка, precision/recall) → real-world пилот на 1 BSL-проекте с зафиксированными порогами. Phase 2 (DeepAgents / LiteLLM как optional backends, skills, memory L0–L3) — по триггерам, не по календарю.

## Принципы

1. **Hexagonal.** Харнесс знает только порты (`LLMPort`, верификатор); провайдеры и сенсоры — заменяемые адаптеры. Смена модели/инструмента = адаптер + строчка конфига, не редизайн.
2. **Тонкие модули.** Каждый пакет решает одну задачу и не тянет чужих зависимостей: `bsl-verify` — чистый stdlib, `russian-llm-pack` — openai+yaml.
3. **Секреты только в env.** Ни одного ключа в коде, тестах, конфигах, истории. Провайдеры ссылаются на *имена* переменных окружения.
4. **Тесты без мира.** Юнит-тесты не ходят в сеть, не зовут java, не тратят токены. Живые проверки — отдельные маркеры (`live`, `integration`), запускаются явно.
5. **Проверяй реальность, а не предположения.** Форматы внешних инструментов фиксируются живым прогоном и попадают в тест-фикстуры (см. `bsl-verify` парсер против реального отчёта v1.0.7).

## Структура

```
ai-harness-os/
├── packages/
│   ├── russian-llm-pack/     # LLM-слой (порт, роутер, fallback, rlp CLI)
│   ├── bsl-verify/           # BSL-верификация (L0/L1 сенсор, bsl-check CLI)
│   ├── harness-loop/         # agent loop (генерация→верификация→фикс, harness-loop CLI)
│   └── agents-md/            # генератор AGENTS.md (анализ + шаблоны + валидатор)
├── scripts/
│   └── setup.sh              # установка+тестирование за один запуск (Git Bash/WSL/Linux)
├── .github/workflows/ci.yml  # матрица: пакет × python 3.10–3.13
├── AGENTS.md                 # правила для AI-агентов, работающих с репо
├── CHANGELOG.md              # новости репозитория
└── LICENSE                   # MIT
```

## Быстрый старт

### Вариант 1 — одним скриптом (Linux / macOS / WSL / Git Bash на Windows)

```bash
bash scripts/setup.sh --clone
# если репо уже склонирован (из корня):  bash scripts/setup.sh
# полезные флаги: --live (живой LLM-smoke, ~1 цент), --with-jar (качать
# bsl-language-server 124 МБ), --skip-tests
```

Скрипт сам: клонирует → найдёт Python 3.10+ → создаст `.venv` → поставит все 4
пакета → прогонит юнит-тесты (без сети/java/ключей) → проверит CLI (`rlp check`,
`harness-loop doctor`). В конце подскажет команду судейского прогона.

### Вариант 2 — вручную (то же самое по шагам)

```bash
git clone https://github.com/Pradushkoai/ai-harness-os
cd ai-harness-os

# 1) venv — ОБЯЗАТЕЛЬНО (в корне репо нет pyproject.toml, пакеты ставятся
#    из подпапок packages/*; без venv на Windows pip обычно ломается)
python -m venv .venv
source .venv/bin/activate    # Git Bash/WSL; PowerShell: .venv\Scripts\Activate.ps1

# 2) все 4 пакета (editable + dev-зависимости)
pip install -e "packages/russian-llm-pack[dev]"
pip install -e "packages/bsl-verify[dev]"
pip install -e "packages/harness-loop[dev]"
pip install -e "packages/agents-md[dev]"

# 3) ключи — только через env (никогда в файлах репо)
export DEEPSEEK_API_KEY=sk-...    # генератор по умолчанию
export QWEN_API_KEY=sk-...        # судья по умолчанию (алиас DASHSCOPE_API_KEY)
# PowerShell: $env:DEEPSEEK_API_KEY="sk-..."; $env:QWEN_API_KEY="sk-..."

# 4) LLM-слой: что видно, какие цепочки
rlp check
rlp chat "привет" --max-tokens 32

# 5) BSL-верификация: нужны java 17+ и jar
#    jar: скачать bsl-language-server-*-exec.jar (releases 1c-syntax)
#    и положить в ~/.bsl-language-server/bsl-language-server.jar
#    (или переменная BSL_LS_JAR) — см. `bash scripts/setup.sh --with-jar`
bsl-doctor
bsl-check src/

# 6) Agent loop: генерация → верификация → исправление (+ судья, + бенчмарк)
harness-loop doctor
harness-loop run "Напиши функцию СуммаДвухЧисел(А, Б)" --save module.bsl
harness-loop run "Напиши функцию проверки ИНН" --judge   # судья: qwen/qwen-max
harness-loop eval --judge --save-report report.json --markdown report.md --verbose
harness-loop eval --difficulty hard      # срез для сравнения моделей
harness-loop eval --judge --no-judge-reference   # A/B: судья без эталонов

# 7) Генератор AGENTS.md (для любого проекта, чистый stdlib)
agents-md init ~/projects/my-1c-config
agents-md validate ~/projects/my-1c-config
```

Схема по умолчанию с RLP v0.3: **DeepSeek кодит — Qwen судит** (судья ≠ модель-генератор,
judge-цепь: `qwen/qwen-max → zai → deepseek → yandexgpt`; провайдеры без ключей просто
пропускаются). Китайский Qwen-аккаунт: `providers: {qwen: {base_url: https://dashscope.aliyuncs.com/compatible-mode/v1}}` в `rlp.config.yaml`.

## Разработка

```bash
bash scripts/setup.sh                 # или всё руками, как выше
cd packages/russian-llm-pack && pytest -q          # без сети
cd packages/bsl-verify && pytest -q                # без java
cd packages/harness-loop && pytest -q              # без сети/java/токенов
cd packages/agents-md && pytest -q                 # tmp-проекты, ничего внешнего
BSL_LS_JAR=... pytest -m integration -v            # живой bsl LS (bsl-verify, harness-loop)
DEEPSEEK_API_KEY=... pytest -m live -v             # живой LLM (russian-llm-pack, harness-loop)
QWEN_API_KEY=... pytest -m live -v                 # + живой smoke Qwen
```

## Лицензия

MIT — см. [LICENSE](LICENSE).
