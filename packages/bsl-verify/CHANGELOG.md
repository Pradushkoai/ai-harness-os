# Changelog — bsl-verify

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/), версионирование — [SemVer](https://semver.org/lang/ru/).

## [0.1.1] — 2026-09-30

### Fixed
- `bsl-check --version` и `bsl-doctor --version` падали с exit 2: консольные entry points приклеивают subcommand вперёд аргументов (`main_check` → `_main(["check", ...])`), поэтому `--version` разбирался сабпарсером, у которого его не было. Сабпарсеры `check`/`doctor` получили собственный `--version`. Это чинило красный CI smoke (`bsl-check --version`) для всей матрицы bsl-verify.
- Добавлены regression-тесты на консольные entry points (`main_check(["--version"])` / `main_doctor(["--version"])`) и на явную форму `check --version` — прежний тест проверял только `_main(["--version"])`, путь, который реальные скрипты не используют.

## [0.1.0] — 2026-09-30

### Added
- `BslVerifier` — фасад с тремя сценариями: `verify_dir()` (проект целиком), `verify_files()` (точные файлы через staging с маппингом путей обратно), `verify_module_text()` (сенсор agent loop: проверка сгенерированного кода до записи на диск).
- Парсер реального формата `analyze -r json` bsl-language-server v1.0.7: `fileinfos`, `file://` URI (включая Windows-диски и `../..`-сегменты относительно cwd), PascalCase-severity, метрики (строки, процедуры/функции, сложность). Толерантен к эволюции формата.
- `VerifyPolicy` — политика как код: `max_errors` (дефолт 0 — любой Error = провал), `max_warnings`, `ignore_codes`, `only_codes`. Отфильтрованные диагностики исчезают из счёта, вывода и JSON согласованно.
- Runner: дискавери java (`BSL_JAVA` → `JAVA_HOME` → PATH) и jar (`BSL_LS_JAR` → `./` → `~/.bsl-language-server/`), сборка команды `analyze -s … -r json -o … -q [-c …]`, таймауты, человекочитаемые ошибки окружения.
- CLI: `bsl-check` (файлы/директории, `--json`, `--max-errors/--max-warnings`, `--ignore/--only`, `--verbose`; exit 0/1/2) и `bsl-doctor` (диагностика окружения с подсказками).
- 82 юнит-теста на моках (фикстура — реальный отчёт v1.0.7) + 6 интеграционных на живом сервере (маркер `integration`).
- Зависимости: чистый stdlib.
