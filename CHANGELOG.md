# Changelog — ai-harness-os (репозиторий)

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/). История отдельных пакетов — в их собственных CHANGELOG.

## [0.8.0] — 2026-10-01

### Added
- **`harness-loop` v0.5.0 — reference-aware judge (judge-протокол SWE-bench-BSL v0.4) + L2-метрики в отчётах.** В eval-прогонах судья видит эталонное решение и сравнивает **семантику** модуля-кандидата с эталоном (формулы, границы условий, off-by-one, граничные случаи, возвращаемый результат) — отдельный системный промпт `JUDGE_REFERENCE_SYSTEM_PROMPT`. Инвариант анти-читинга: эталон получает ТОЛЬКО судья, генератор — никогда (модель скопирует эталон → бенчмарк обесценится); инвариант протестирован на трёх уровнях (loop / run_eval / CLI). Отчёты eval получили L2-агрегаты: `judge_mode` (none/plain/reference), approved/vetoed, avg score, judge-поля в задачах, секция «Ревьюер (L2)» в markdown. Флаг `--no-judge-reference` — A/B со старым режимом. `LoopResult.judge` теперь хранит последний вердикт даже при вето, пережившем бюджет (score/issues отклонённых задач больше не теряются). 158 → 191 юнит-тест.

## [0.7.0] — 2026-10-01

### Added
- **`harness-loop` v0.4.0 — Mini SWE-bench-BSL v0.3: 30 → 54 задачи** (Phase 1 по вердикту; ответ на насыщение бенчмарка — живой прогон v0.1 дал 10/10). Новые категории **table** (таблицы значений) и **numbers** (включая контрольную сумму ИНН), расширены query/structure/dates/collections/string/errors. Итог: 11 категорий, 10 easy / 31 medium / **13 hard** (было 4). Все 54 эталона прошли реальный bsl-language-server с 0 Error (интеграционный гейт). Отчёты получили разбивку по категориям и сложности («где модель слаба»), в CLI добавлены фильтры `eval --category` / `--difficulty` для сравнения моделей на подмножествах. 146 → 158 юнит-тестов.

## [0.6.0] — 2026-10-01

### Added
- **`russian-llm-pack` v0.2.0 — нативные адаптеры RU-провайдеров** (Phase 0 по вердикту: «пресет-заглушка не даёт fallback»). **YandexGPT**: протокол Yandex Foundation Models (modelUri, {role, text}, x-folder-id), Api-Key или IAM-токен, `YANDEXGPT_FOLDER_ID` обязателен, файнтюн-URI (`ds://…`) проходят как есть. **GigaChat**: OAuth Basic-флоу внутри адаптера (access-токен ~30 мин, авто-refresh за 60с до истечения, RqUID per request) либо готовый токен; TLS российского CA — верификация по умолчанию, при ошибке хендшейка понятная ошибка с фиксами (`GIGACHAT_CA_BUNDLE` / `GIGACHAT_ALLOW_INSECURE=1`). Оба — на чистом stdlib (новый общий `providers/_http.py`), наружу тот же `LLMPort`. `yandexgpt` замыкает все builtin-цепи: сетап с одним лишь Яндекс-аккаунтом работоспособен. 63 → 125 юнит-тестов (фейковый транспорт) + live-smoke для обоих (скип без ключей).
- **`harness-loop` v0.3.0 — бенчмарк 10 → 30 задач** (Phase 1 по вердикту: «10-50 задач»). Новые категории dates / collections / query, 4 hard-задачи, интеграционный гейт: каждое reference-решение проходит реальный bsl-language-server с 0 Error (один JVM-прогон на весь набор). 144 → 146 юнит-тестов. Починен мёртвый флаг `eval --context`.
- **Первый живой прогон бенчмарка (юзер, Windows): mini SWE-bench-BSL v0.1 = 10/10 (100%)**, 13 итераций, ~6.5K токенов — конвейер работает end-to-end на реальном DeepSeek + реальном LS. Фиксация: benchmark saturated, рост до 30 задач — ответ.

## [0.5.0] — 2026-10-01

### Added
- **`harness-loop` v0.2.0 — judge-цепь (L2), телеметрия Langfuse, mini SWE-bench-BSL.** Второе мнение другой модели о коде, прошедшем верификатор: строгий формат вердикта + толерантный парсер, вето — только явное (верификатор остаётся авторитетом), недоступность judge не отбрасывает рабочий код. Телеметрия в Langfuse поверх швов `on_event`/`on_iteration` (stdlib, без SDK, ключи только env, no-op без ключей). Первый публичный BSL-бенчмарк: 10 задач с reference-решениями, `harness-loop eval` с отчётами JSON/markdown. 144 юнит-теста (было 63) + 3 интеграционных с реальным bsl LS — зелёные.

## [0.4.0] — 2026-09-30

### Added
- **Пакет `agents-md` v0.1.0** — генератор AGENTS.md для AI-агентов: анализ проекта (1C-EDT / 1C-XML / python / js-ts / generic; приоритет 1С), структурный сканер с RU-комментариями для директорий метаданных 1С, генератор RU-шаблонов по agents.md-конвенциям (лимит 32 KiB), валидатор (UTF-8, обязательные разделы с RU-алиасами, предупреждения). CLI `agents-md init/validate` (exit 0/1/2, `--version` на всех уровнях). Чистый stdlib. 82 юнит-теста на герметичных tmp-проектах; dogfood: генератор и валидатор прогнаны на самом монорепо.
- CI: `agents-md` в матрице «пакет × Python», smoke `agents-md --version`.

## [0.3.0] — 2026-09-30

### Added
- **Пакет `harness-loop` v0.1.0** — первый продукт харнесса: цикл «генерация → верификация → исправление» (backpressure L0/L1) над `LLMPort` (russian-llm-pack) и `BslVerifier` (bsl-verify). Извлечение BSL из ответа модели (фенсы/эвристики), fix-промпты с диагностиками верификатора, бюджет итераций, полная телеметрия итераций (токены/латентности/диагностики), CLI `harness-loop run/doctor` (exit 0/1/2). 63 юнит-теста на фейках + 3 интеграционных (fake LLM + реальный bsl LS: полный цикл ломает→чинит) + live-тест. Роутинг и fallback — через `RouterPort`, ошибка любого слоя не роняет цикл.
- CI: установка всех пакетов в порядке зависимостей (russian-llm-pack → bsl-verify → harness-loop), smoke для `harness-loop --version`.

## [0.2.1] — 2026-09-30

### Fixed
- **CI smoke для bsl-verify был красным:** `bsl-check --version` (и `bsl-doctor --version`) возвращали exit 2 — консольные entry points приклеивают subcommand вперёд аргументов, а сабпарсеры не знали `--version`. Починено в `bsl-verify` v0.1.1, добавлены regression-тесты на реальные entry points.

## [0.2.0] — 2026-09-30

### Changed
- **Реструктуризация в монорепозиторий `packages/`:** `russian-llm-pack` переехал из корня в `packages/russian-llm-pack/` (git mv, история сохранена). Импорт и CLI не изменились — только путь установки: `pip install -e packages/russian-llm-pack`.
- CI: матрица «пакет × Python 3.10–3.13», smoke CLI для каждого пакета.

### Added
- **Пакет `bsl-verify` v0.1.0** — адаптер статической проверки 1C/BSL поверх bsl-language-server (проверено на v1.0.7): парсер реального JSON-отчёта (fileinfos/URI/PascalCase-severity/метрики), policy-движок (max_errors/max_warnings/ignore/only), staging с маппингом путей, `verify_module_text()` для agent loop, CLI `bsl-check`/`bsl-doctor`, 82 юнит-теста + 6 живых интеграционных. Чистый stdlib.
- Корневой README монорепо, AGENTS.md дополнен правилами bsl-verify.

## [0.1.0] — 2026-09-30

### Added
- Первый коммит: пакет `russian-llm-pack` v0.1.0 (LLMPort, generic OpenAI-compatible адаптер, пресеты deepseek/zai/gigachat/yandexgpt, роутер с fallback, CLI `rlp`, 63 юнит-теста + живой smoke на DeepSeek, CI).
