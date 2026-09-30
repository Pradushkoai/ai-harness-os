# Changelog — ai-harness-os (репозиторий)

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/). История отдельных пакетов — в их собственных CHANGELOG.

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
