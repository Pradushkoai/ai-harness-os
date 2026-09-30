# bsl-verify

Адаптер статической проверки 1С/BSL поверх [bsl-language-server](https://github.com/1c-syntax/bsl-language-server). Это **сенсор backpressure L0/L1** будущего ИИ-харнесса для 1С: агент написал или поправил код → `bsl-verify` за секунды возвращает структурированные диагностики (ParseError = L0, ~250 диагностик = L1) → агент чинит, пока не зашло дорого.

Часть экосистемы **ai-harness-os**. Зависимости: **чистый stdlib** — ни сети, ни секретов, ни сторонних пакетов.

## Зачем это

- **Быстрая обратная связь для agent loop.** Семантическая проверка (Напарник) стоит минуты и деньги; синтаксис и диагностики bsl-language-server — секунды и ноль. Правильный порядок: сначала дешёвый sensor, потом дорогой.
- **Политика как код.** Дефолт: любой `Error` = провал (агент обязан починить до продолжения). Пороги, ignore/whitelist кодов — параметрами.
- **Проверка кода, которого ещё нет на диске.** `verify_module_text()` — написали текст → проверили → выбросили. Ключевой метод для генерации кода агентом.
- **Метрики бесплатно.** Строки, процедуры/функции, цикломатическая/когнитивная сложность из того же отчёта.

## Установка

```bash
cd ai-harness-os/packages/bsl-verify
pip install -e .
```

Нужно окружение:

1. **Java 17+** (`java -version`) — или переменные `BSL_JAVA` / `JAVA_HOME`.
2. **bsl-language-server.jar** — скачать `*-exec.jar` с [releases](https://github.com/1c-syntax/bsl-language-server/releases), затем любой вариант:
   - `export BSL_LS_JAR=/path/to/bsl-language-server.jar`
   - или положить в `~/.bsl-language-server/bsl-language-server.jar`
   - или в корень проекта как `./bsl-language-server.jar`

Проверка окружения:

```bash
bsl-doctor
```

## Использование

### CLI

```bash
# проверить файлы/директорию (рекурсивно по .bsl/.os)
bsl-check src/
bsl-check module.bsl another.bsl

# политика: ошибки допускаются, предупреждения — нет
bsl-check src/ --max-errors 0 --max-warnings 10

# игнорировать конкретные диагностики
bsl-check src/ --ignore ParseError,EmptyCodeBlock

# машиночитаемый вывод (для скриптов и агентов)
bsl-check module.bsl --json

# явные пути к java и jar
bsl-check module.bsl --java /usr/bin/java --jar /opt/bsl-language-server.jar
```

Коды выхода: `0` — политика пройдена, `1` — есть нарушения, `2` — проблема окружения.

Вывод:

```
BSL check: 1 file(s), 2 diagnostic(s): 1 error, 1 warning, 0 info, 0 hint [FAILED; policy VerifyPolicy(max_errors=0); 2.1s]
  ! errors: 1 > allowed 0
  ...module_broken.bsl:4:1 ERROR ParseError: Ошибка разбора исходного кода. Ожидался один из следующих токенов: ...
```

### API

```python
from bsl_verify import BslVerifier, VerifyPolicy

verifier = BslVerifier()  # env-дискавери java + jar

# главный сценарий харнесса: проверить СГЕНЕРИРОВАННЫЙ код
result = verifier.verify_module_text("""
Процедура МойМодуль()
    Сообщить("привет");
КонецПроцедуры
""")
print(result.passed)          # False если есть Error
print(result.summary_lines()) # читаемый отчёт для агента

# инкрементальная проверка изменённых файлов
result = verifier.verify_files(["src/МодульЗаказа.bsl"])

# проверка всего проекта
result = verifier.verify_dir("src/")

# мягкая политика: только определённые коды
strict = BslVerifier(policy=VerifyPolicy(only_codes=frozenset({"ParseError"})))
```

## Как это работает

```
BslVerifier.verify_module_text(text)      ← сценарий agent loop
        │  temp staging dir + module.bsl
        ▼
   java -jar bsl-language-server.jar analyze -s <staging> -r json -o <out> -q
        │
        ▼  <out>/bsl-json.json  (формат проверен на v1.0.7)
   parser: fileinfos → FileReport[Diagnostic, FileMetrics]
        │  path mapping: staging → исходные пути
        ▼
   VerifyPolicy.evaluate() → VerifyResult(passed, counts, violations)
```

- `verify_files()` копирует точные файлы в staging-директорию (чтобы не анализировать соседей), отчёт мапит обратно на оригинальные пути; коллизии имён решаются префиксом.
- `verify_dir()` анализирует директорию как есть.
- Парсер устойчив к эволюции формата: отсутствующие поля, вариации severity, `file://` URI с Windows-дисками — не роняют проверку.

## Разработка

```bash
pip install -e ".[dev]"
pytest -q                                  # юнит-тесты: моки, без java
BSL_LS_JAR=/path/to/bsl-language-server.jar pytest -m integration -v   # живая проверка
```

Фикстура `tests/fixtures/sample_report.json` — **реальный** вывод bsl-language-server v1.0.7, тесты парсера гоняются по нему.

## Roadmap

- **v0.1** — парсер/роутер/CLI как есть ✅
- **v0.2** — инкрементальный режим (кеши по mtime), конфиг bsl LS (`-c`) с проектными пресетами, SARIF-репортер
- **v0.3** — интеграция в agent loop харнесса как backpressure-порт L0/L1, телеметрия в Langfuse

## Лицензия

MIT. См. [корневой LICENSE](../../LICENSE).
