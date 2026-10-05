# ai-harness-os

Открытые пакеты ИИ-инфраструктуры для разработки под 1С (и не только). Монорепозиторий: каждый пакет — самостоятельный distributable со своим релизным циклом.

Позиционирование: **сборка харнесса для 1С-разработки из тонких, тестируемых модулей** — LLM-слой, сенсоры верификации, генераторы контекста — без переусложнения и с санкционно-устойчивым стеком.

## Пакеты

| Пакет | Что это | Статус |
|---|---|---|
| [`russian-llm-pack`](packages/russian-llm-pack/) | Единый LLM-порт: DeepSeek / Z.ai / Qwen / GigaChat (OAuth) / YandexGPT (нативный), YAML-роутинг задач, fallback-цепочки, CLI `rlp` | v0.3.0 — DeepSeek живой, Qwen ведёт judge-цепь |
| [`bsl-verify`](packages/bsl-verify/) | Статическая проверка 1С/BSL через bsl-language-server: backpressure L0/L1, политика как код, CLI `bsl-check` / `bsl-doctor` | v0.1.1 — работает вживую (v1.0.7 LS) |
| [`harness-loop`](packages/harness-loop/) | Цикл «генерация → верификация → (ревью) → исправление»: LLM пишет BSL-модуль, верификатор гейтит, judge (вторая модель) накладывает вето — в eval судья сравнивает семантику с эталоном (reference-aware, генератор его не видит); телеметрия Langfuse; mini SWE-bench-BSL (70 задач, 15 категорий incl. nstr/http/skd/tablepart — реальные 1С-паттерны, 18 hard; фильтры `--category`/`--difficulty`; L2-агрегаты в отчётах); L1-оракул исполнения (OneScript, 59/70 задач с живыми чеками incl. сетевые через локальный стаб); CLI `harness-loop run/eval/doctor` | v0.9.0 — оракул L1 (59/70 с http-стабом) + контекстный слой |
| [`agents-md`](packages/agents-md/) | Генератор AGENTS.md для AI-агентов: анализ проекта (1C-EDT / 1C-XML / python / js-ts / generic), RU-шаблоны, валидатор стандарта; CLI `agents-md init/validate` | v0.1.0 — чистый stdlib, dogfooded на этом репо |
| [`harness-mcp`](packages/harness-mcp/) | MCP-выход: stdio-сервер Model Context Protocol — 5 тулингов поверх готовых портов (`ping`, `benchmark_info`, `verify_module`, `run_loop`, `eval_summary`), сессионный кэш run_id с TTL; конфиги для Claude Code / opencode / Cline; CLI `harness-mcp` | v0.1.0 — фаза B дорожной карты 2.1 |

**Дальше по плану (дорожная карта 2.1, 12 недель):** ~~исполнительный оракул L1~~ ✅ сделано (harness-loop 0.7.0: ExecutorPort + OneScriptRunner, метрики L0/L1/L2) → ~~доводка эталонов~~ ✅ 59/70 задач с живыми чеками (84%, потолок исчерпан: 11 несовместимостей задокументированы с причинами; сетевые — под локальным стабом, 0.8.0) → ~~пакет `harness-mcp`~~ ✅ сделано (0.1.0: stdio MCP-сервер, 5 тулингов, сессионный кэш, smoke с реальным клиентом — Claude Code / opencode / Cline) → ~~контекстный слой~~ ✅ сделано (0.9.0: `ContextProviderPort` + `BuiltInIndexer` из коробки + `McpIndexerBackend` с тихим фоллбеком, `--context-project`, наблюдаемость в IterationLog, A/B-скрипт) → калибровка судьи (ручная разметка, precision/recall) → real-world пилот на 1 BSL-проекте с зафиксированными порогами. Phase 2 (DeepAgents / LiteLLM как optional backends, skills, memory L0–L3) — по триггерам, не по календарю.
