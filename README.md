# ai-harness-os

Открытые пакеты ИИ-инфраструктуры для разработки под 1С (и не только). Монорепозиторий: каждый пакет — самостоятельный distributable со своим релизным циклом.

Позиционирование: **сборка харнесса для 1С-разработки из тонких, тестируемых модулей** — LLM-слой, сенсоры верификации, генераторы контекста — без переусложнения и с санкционно-устойчивым стеком.

## Пакеты

| Пакет | Что это | Статус |
|---|---|---|
| [`russian-llm-pack`](packages/russian-llm-pack/) | Единый LLM-порт: DeepSeek / Z.ai / GigaChat (OAuth) / YandexGPT (нативный), YAML-роутинг задач, fallback-цепочки, CLI `rlp` | v0.2.0 — DeepSeek живой, нативные RU-адаптеры готовы |
| [`bsl-verify`](packages/bsl-verify/) | Статическая проверка 1С/BSL через bsl-language-server: backpressure L0/L1, политика как код, CLI `bsl-check` / `bsl-doctor` | v0.1.1 — работает вживую (v1.0.7 LS) |
| [`harness-loop`](packages/harness-loop/) | Цикл «генерация → верификация → (ревью) → исправление»: LLM пишет BSL-модуль, верификатор гейтит, judge (вторая модель) накладывает вето — в eval судья сравнивает семантику с эталоном (reference-aware, генератор его не видит); телеметрия Langfuse; mini SWE-bench-BSL (54 задачи, 11 категорий, 13 hard; фильтры `--category`/`--difficulty`; L2-агрегаты в отчётах); CLI `harness-loop run/eval/doctor` | v0.5.0 — эталоны всех 54 задач проверены реальным LS |
| [`agents-md`](packages/agents-md/) | Генератор AGENTS.md для AI-агентов: анализ проекта (1C-EDT / 1C-XML / python / js-ts / generic), RU-шаблоны, валидатор стандарта; CLI `agents-md init/validate` | v0.1.0 — чистый stdlib, dogfooded на этом репо |

**Дальше по плану:** live-прогон 54 задач у юзера с reference-aware judge (L1+L2-цифры) → рост набора под реальные 1С-проекты (НСтр, СКД, HTTP, табличные части) → real-world пилот на 1 BSL-проекте → движок DeepAgents как optional backend на тех же портах (Phase 2 вердикта).

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
├── .github/workflows/ci.yml  # матрица: пакет × python 3.10–3.13
├── AGENTS.md                 # правила для AI-агентов, работающих с репо
├── CHANGELOG.md              # новости репозитория
└── LICENSE                   # MIT
```

## Быстрый старт

```bash
git clone https://github.com/Pradushkoai/ai-harness-os
cd ai-harness-os

# LLM-слой
pip install -e packages/russian-llm-pack
export DEEPSEEK_API_KEY=sk-...
rlp check && rlp chat "привет"

# BSL-верификация (нужны java 17+ и bsl-language-server.jar)
pip install -e packages/bsl-verify
bsl-doctor                 # что не хватает и как починить
bsl-check src/             # проверка с диагностиками и exit-кодами

# Agent loop: генерация → верификация → исправление (+ judge, + бенчмарк)
pip install -e "packages/harness-loop[dev]"
harness-loop doctor        # оба слоя: ключи + java/jar
harness-loop run "Напиши функцию СуммаДвухЧисел(А, Б)" --save module.bsl
harness-loop run "Напиши функцию проверки ИНН" --judge --judge-chain judge
harness-loop eval --markdown report.md   # mini SWE-bench-BSL: 54 задачи, отчёт
harness-loop eval --difficulty hard      # только hard-подмножество (сравнение моделей)
harness-loop eval --judge --no-judge-reference  # A/B: судья без эталонов

# Генератор AGENTS.md (для любого проекта, чистый stdlib)
pip install -e packages/agents-md
agents-md init ~/projects/my-1c-config   # анализ → черновик AGENTS.md
agents-md validate ~/projects/my-1c-config
```

## Разработка

```bash
pip install -e "packages/russian-llm-pack[dev]"
pip install -e "packages/bsl-verify[dev]"
pip install -e "packages/harness-loop[dev]"
pip install -e "packages/agents-md[dev]"
cd packages/russian-llm-pack && pytest -q          # без сети
cd packages/bsl-verify && pytest -q                # без java
cd packages/harness-loop && pytest -q              # без сети/java/токенов
cd packages/agents-md && pytest -q                 # tmp-проекты, ничего внешнего
BSL_LS_JAR=... pytest -m integration -v            # живой bsl LS (bsl-verify, harness-loop)
DEEPSEEK_API_KEY=... pytest -m live -v             # живой LLM (russian-llm-pack, harness-loop)
```

## Лицензия

MIT — см. [LICENSE](LICENSE).
