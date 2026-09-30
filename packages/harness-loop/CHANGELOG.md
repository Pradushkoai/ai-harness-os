# Changelog — harness-loop

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/), версионирование — [SemVer](https://semver.org/lang/ru/).

## [0.1.0] — 2026-09-30

### Added
- `BslAgentLoop` — цикл «генерация → верификация → исправление» над двумя портами: `LLMPort` (russian-llm-pack) и `BslVerifier` (bsl-verify). Стоп-условия: политика пройдена / бюджет итераций (`budget_exhausted`) / ошибка LLM (`llm_error`) / ошибка окружения верификатора (`verifier_error`) — доменные ошибки не бросаются наружу.
- `RouterPort` — адаптер Router → LLMPort: прикалывает чейн роутинга (`coding`/`judge`/...), точка входа fallback-логики в цикл.
- Извлечение BSL из ответа модели (`extract_bsl_code`): фенсы ```bsl/без тега/с чужим тегом, несколько фенсов (побеждает последний), чистый BSL без фенса; проза без кода — итерация с замечанием о формате.
- Промпты (RU): системный BSL-кодер, task-промпт, fix-промпт с прошлым кодом и диагностиками верификатора; кап строк диагностики с явным «... и ещё N».
- `IterationLog`/`LoopResult`: токены и латентности по итерациям и суммарно, счётчики диагностик, `summary_lines()` и `to_dict()`.
- CLI: `harness-loop run` (`--context/--context-file`, `--max-iterations`, `--chain/--model`, `--save`, `--json`, `--verbose`, policy-флаги) и `harness-loop doctor` (проверка обоих слоёв: ключи провайдеров + java/jar). Exit-коды 0/1/2. `--version` на обоих уровнях парсера (регрессия bsl-check закрыта с рождения).
- 46 юнит-тестов на фейках + 3 интеграционных (fake LLM + реальный bsl LS: полный backpressure-цикл ломает→чинит) + live-тест (реальный LLM, маркер `live`).
