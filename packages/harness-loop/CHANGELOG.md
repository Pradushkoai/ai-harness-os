# Changelog — harness-loop

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/), версионирование — [SemVer](https://semver.org/lang/ru/).

## [0.2.0] — 2026-10-01

### Added
- **Judge-цепь (L2, второе мнение).** `Judge` над любым LLMPort (на практике — свой `RouterPort`, чейн `--judge-chain`): смотрит задачу + сгенерированный модуль + остаточные диагностики, отвечает строгим форматом `VERDICT/SCORE/ISSUES/REASONING`. Толерантный парсер (RU-секции и RU-вердикты; вето только явное — верификатор остаётся авторитетом), `JudgeVerdict`, `judge_feedback()`. Вето отправляет замечания в `judge_fix_prompt` → ещё одна итерация; `failure_reason="judge_rejected"` если вето пережило бюджет. Недоступность judge-LLM (`RLLError`) НЕ отбрасывает код, прошедший верификатор: `passed=True` + громкий `judge_error` (surface, не молчание).
- **Телеметрия Langfuse** (`LangfuseTelemetry`): ноль зависимостей (stdlib urllib → `POST /api/public/ingestion`), два шва — `on_router_event` (события роутера: skip/retry/error/ok с usage) и `on_iteration` (итерации: токены, латентности, счётчики, judge). Ключи только по именам env (`LANGFUSE_PUBLIC_KEY/SECRET_KEY/HOST`), нет ключей — тихий no-op, сетевые ошибки проглатываются, код и промпты в пейлоады не попадают. CLI-флаг `--langfuse`, doctor показывает статус ключей.
- **Mini SWE-bench-BSL** (стратегический asset — первого публичного BSL-бенчмарка не существует): формат задач YAML (`id/category/difficulty/prompt/context/reference`), `load_tasks()` с валидацией, `run_eval()` над обычным циклом, `EvalReport` (`summary_lines/to_dict/to_markdown/save_json/save_markdown`). Встроенный набор v0: 10 задач (function/loop/branching/errors/string/structure). Reference-решения репортятся, гейтинга по ним нет (строковое сравнение кода бессмысленно). CLI `harness-loop eval` (`--tasks/--limit/--judge/--langfuse/--save-report/--markdown/--json`), exit 0 при любом pass rate.
- CLI `run`: флаги `--judge`, `--judge-chain`, `--judge-model`, `--langfuse`; verbose-прогресс показывает judge-вердикты; JSON-выдача дополнена блоками `judge`/`judge_error`/`totals.judge_ms`.
- `IterationLog`: поля `judge_verdict/judge_issues/judge_ms`; `LoopResult`: `judge`, `judge_error`, `total_judge_ms`, причина `judge_rejected`.
- 144 юнит-теста (было 63): +25 парсер/промпты judge, +10 интеграция цикл×judge, +14 телеметрия (транспорт мокается), +19 evals, +13 CLI (judge/eval/langfuse/doctor).

### Changed
- Зависимость `pyyaml>=6.0` — явно (eval-задачи), ранее тянулась транзитивно через russian-llm-pack.

## [0.1.0] — 2026-09-30

### Added
- `BslAgentLoop` — цикл «генерация → верификация → исправление» над двумя портами: `LLMPort` (russian-llm-pack) и `BslVerifier` (bsl-verify). Стоп-условия: политика пройдена / бюджет итераций (`budget_exhausted`) / ошибка LLM (`llm_error`) / ошибка окружения верификатора (`verifier_error`) — доменные ошибки не бросаются наружу.
- `RouterPort` — адаптер Router → LLMPort: прикалывает чейн роутинга (`coding`/`judge`/...), точка входа fallback-логики в цикл.
- Извлечение BSL из ответа модели (`extract_bsl_code`): фенсы ```bsl/без тега/с чужим тегом, несколько фенсов (побеждает последний), чистый BSL без фенса; проза без кода — итерация с замечанием о формате.
- Промпты (RU): системный BSL-кодер, task-промпт, fix-промпт с прошлым кодом и диагностиками верификатора; кап строк диагностики с явным «... и ещё N».
- `IterationLog`/`LoopResult`: токены и латентности по итерациям и суммарно, счётчики диагностик, `summary_lines()` и `to_dict()`.
- CLI: `harness-loop run` (`--context/--context-file`, `--max-iterations`, `--chain/--model`, `--save`, `--json`, `--verbose`, policy-флаги) и `harness-loop doctor` (проверка обоих слоёв: ключи провайдеров + java/jar). Exit-коды 0/1/2. `--version` на обоих уровнях парсера (регрессия bsl-check закрыта с рождения).
- 46 юнит-тестов на фейках + 3 интеграционных (fake LLM + реальный bsl LS: полный backpressure-цикл ломает→чинит) + live-тест (реальный LLM, маркер `live`).
