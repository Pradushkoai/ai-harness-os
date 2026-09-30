# ai-harness-os

Открытые пакеты ИИ-инфраструктуры для разработки под 1С (и не только). Монорепозиторий: каждый пакет — самостоятельный distributable со своим релизным циклом.

Позиционирование: **сборка харнесса для 1С-разработки из тонких, тестируемых модулей** — LLM-слой, сенсоры верификации, генераторы контекста — без переусложнения и с санкционно-устойчивым стеком.

## Пакеты

| Пакет | Что это | Статус |
|---|---|---|
| [`russian-llm-pack`](packages/russian-llm-pack/) | Единый LLM-порт: DeepSeek / Z.ai / GigaChat / YandexGPT, YAML-роутинг задач, fallback-цепочки, CLI `rlp` | v0.1.0 — работает вживую |
| [`bsl-verify`](packages/bsl-verify/) | Статическая проверка 1С/BSL через bsl-language-server: backpressure L0/L1, политика как код, CLI `bsl-check` / `bsl-doctor` | v0.1.0 — работает вживую (v1.0.7 LS) |

**Дальше по плану:** AGENTS.md Generator → Skill Registry client → интеграция в agent loop (DeepAgents + LangGraph).

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
│   └── bsl-verify/           # BSL-верификация (L0/L1 сенсор, bsl-check CLI)
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
```

## Разработка

```bash
pip install -e "packages/russian-llm-pack[dev]"
pip install -e "packages/bsl-verify[dev]"
cd packages/russian-llm-pack && pytest -q          # без сети
cd packages/bsl-verify && pytest -q                # без java
BSL_LS_JAR=... pytest -m integration -v            # живой bsl LS (опционально)
DEEPSEEK_API_KEY=... pytest -m live -v             # живой LLM (опционально)
```

## Лицензия

MIT — см. [LICENSE](LICENSE).
